# -*- coding: utf-8 -*-
"""告警通知分发（P1 告警中心通知层，V1.7.1）。

通道：webhook（每条新告警 POST JSON）+ 邮件（每轮同步合并为一封摘要）。
配置：alert_notify_config 单例行（id=1），控制台 API 读写。
去重：alerts.notified_at —— 每条告警只通知一次（at-most-once），恢复后再次
触发会生成新告警行，视为新告警重新通知。分发失败仅记录日志，不阻塞同步。
"""
from __future__ import annotations

import json
import smtplib
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

from zvplatform.repositories.alert_repository import mark_alerts_notified

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_WECOM_HOST_MARKERS = ("qyapi.weixin.qq.com",)
_WECOM_CONTENT_MAX_CHARS = 3800  # 企业微信 markdown 上限 4096 字节，留余量


def ensure_notify_config_table(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_notify_config (
                id INT PRIMARY KEY,
                enabled TINYINT(1) NOT NULL DEFAULT 0,
                min_severity VARCHAR(20) NOT NULL DEFAULT 'critical',
                webhook_url VARCHAR(500) NULL,
                smtp_host VARCHAR(200) NULL,
                smtp_port INT NOT NULL DEFAULT 465,
                smtp_use_ssl TINYINT(1) NOT NULL DEFAULT 1,
                smtp_user VARCHAR(200) NULL,
                smtp_password VARCHAR(200) NULL,
                from_addr VARCHAR(200) NULL,
                to_addrs VARCHAR(500) NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("INSERT IGNORE INTO alert_notify_config (id) VALUES (1)")
        conn.commit()
    finally:
        cursor.close()


def get_notify_config(conn) -> dict:
    ensure_notify_config_table(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM alert_notify_config WHERE id = 1")
        return cursor.fetchone() or {}
    finally:
        cursor.close()


def update_notify_config(conn, patch: dict) -> dict:
    """合并式更新（未提供的键保持原值；smtp_password 传空串表示保持不变）。"""
    ensure_notify_config_table(conn)
    allowed = {
        "enabled", "min_severity", "webhook_url", "smtp_host", "smtp_port",
        "smtp_use_ssl", "smtp_user", "smtp_password", "from_addr", "to_addrs",
    }
    existing = get_notify_config(conn)
    values = {}
    for key in allowed:
        if key not in patch:
            continue
        value = patch[key]
        if key == "smtp_password" and (value is None or str(value) == ""):
            continue  # 空密码 = 保持现有值
        if key == "min_severity" and value not in _SEVERITY_ORDER:
            continue
        values[key] = value
    if values:
        cursor = conn.cursor()
        try:
            columns = ", ".join(f"{key} = %s" for key in values)
            cursor.execute(
                f"UPDATE alert_notify_config SET {columns} WHERE id = 1",
                tuple(values.values()),
            )
            conn.commit()
        finally:
            cursor.close()
    return get_notify_config(conn)


def _hostname_map(conn, asset_ids: list) -> dict:
    if not asset_ids:
        return {}
    cursor = conn.cursor(dictionary=True)
    try:
        placeholders = ",".join(["%s"] * len(asset_ids))
        cursor.execute(
            f"SELECT id, hostname FROM assets WHERE id IN ({placeholders})",
            tuple(asset_ids),
        )
        return {int(r["id"]): r.get("hostname") for r in cursor.fetchall()}
    except Exception:
        return {}
    finally:
        cursor.close()


def _send_webhook(url: str, payload: dict) -> bool:
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code < 400
    except Exception as exc:
        print(f"[AlertNotify] webhook failed: {exc}")
        return False


def _is_wecom_webhook(url: str) -> bool:
    """企业微信群机器人 URL 自动识别（也可扩展钉钉等）。"""
    return any(marker in url for marker in _WECOM_HOST_MARKERS)


def _wecom_markdown_content(pending: list, hostnames: dict) -> str:
    lines = ["**Z-View 告警通知**"]
    for a in pending:
        hostname = hostnames.get(a.get("asset_id")) or str(a.get("asset_id"))
        severity = str(a.get("severity") or "warning")
        color = "warning" if severity == "critical" else "info"
        metric = ""
        cur_v, thr_v = a.get("current_value"), a.get("threshold_value")
        if cur_v is not None and thr_v is not None:
            metric = f"（当前 {cur_v} / 阈值 {thr_v}）"
        lines.append(
            f"> <font color=\"{color}\">[{severity}]</font> "
            f"**{hostname}** — {a.get('message')} {metric}"
        )
    lines.append(f"> 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    content = "\n".join(lines)
    if len(content) > _WECOM_CONTENT_MAX_CHARS:
        content = content[:_WECOM_CONTENT_MAX_CHARS] + "\n> …（内容过长已截断）"
    return content


def _post_wecom_markdown(url: str, content: str) -> tuple[bool, str]:
    """企业微信机器人 markdown 消息（严格校验 errcode）。"""
    try:
        resp = requests.post(url, json={"msgtype": "markdown", "markdown": {"content": content}}, timeout=10)
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code < 400 and data.get("errcode") == 0:
            return True, "ok"
        return False, f"errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
    except Exception as exc:
        return False, str(exc)


def _send_wecom(url: str, pending: list, hostnames: dict) -> tuple[bool, str]:
    """企业微信群机器人：markdown 消息，多条告警合并为一条（规避 20 条/分钟限流）。
    严格校验响应 errcode==0（企业微信格式错误也返回 200，必须看 body）。"""
    return _post_wecom_markdown(url, _wecom_markdown_content(pending, hostnames))


def _wecom_recovery_content(resolved_incidents: list) -> str:
    lines = ["**Z-View 恢复通知**"]
    for inc in resolved_incidents:
        hostname = inc.get("hostname") or str(inc.get("asset_id"))
        count = inc.get("alert_count")
        lines.append(
            f'> <font color="info">✅ **{hostname}** — {count if count is not None else "若干"} 条告警已全部恢复</font>'
        )
        lines.append(f"> 事件：{inc.get('incident_id')}")
    lines.append(f"> 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    content = "\n".join(lines)
    if len(content) > _WECOM_CONTENT_MAX_CHARS:
        content = content[:_WECOM_CONTENT_MAX_CHARS] + "\n> …（内容过长已截断）"
    return content


def dispatch_recovery_notifications(conn, resolved_incidents: list) -> dict:
    """事件恢复通知（V1.9.0）：全部活跃告警恢复的事件推送到已配置通道。

    与告警分发共用启用开关与通道配置；未配置/未启用时静默跳过。
    """
    if not resolved_incidents:
        return {"skipped": "no_resolved_incidents"}
    config = get_notify_config(conn)
    if not config.get("enabled"):
        return {"skipped": "disabled"}

    result: dict = {"channel": None, "sent": False, "error": ""}
    webhook_url = str(config.get("webhook_url") or "").strip()
    if webhook_url:
        if _is_wecom_webhook(webhook_url):
            result["channel"] = "wecom"
            content = _wecom_recovery_content(resolved_incidents)
            ok, err = _post_wecom_markdown(webhook_url, content)
            result["sent"] = ok
            if not ok:
                result["error"] = err
                print(f"[AlertNotify] wecom recovery failed: {err}")
        else:
            result["channel"] = "webhook"
            result["sent"] = _send_webhook(webhook_url, {
                "type": "recovery",
                "incidents": resolved_incidents,
            })

    if not result["sent"] and config.get("smtp_host") and config.get("to_addrs"):
        result["channel"] = (result["channel"] or "") + "+email"
        body = "\n\n".join(
            f"{inc.get('hostname') or inc.get('asset_id')} — {inc.get('alert_count')} 条告警已全部恢复"
            f"（事件 {inc.get('incident_id')}）"
            for inc in resolved_incidents
        )
        result["sent"] = _send_email(
            config,
            f"Z-View 恢复通知：{len(resolved_incidents)} 个事件已恢复",
            body + f"\n\n时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        )
    return result


def _send_email(config: dict, subject: str, body: str) -> bool:
    to_addrs = [a.strip() for a in str(config.get("to_addrs") or "").split(",") if a.strip()]
    host = config.get("smtp_host")
    if not to_addrs or not host:
        return False
    try:
        port = int(config.get("smtp_port") or 465)
        use_ssl = bool(config.get("smtp_use_ssl"))
        from_addr = str(config.get("from_addr") or config.get("smtp_user") or "")
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("Z-View 告警", "utf-8")), from_addr))
        msg["To"] = ", ".join(to_addrs)
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        try:
            if config.get("smtp_user"):
                server.login(str(config.get("smtp_user")), str(config.get("smtp_password") or ""))
            server.sendmail(from_addr, to_addrs, msg.as_string())
        finally:
            server.quit()
        return True
    except Exception as exc:
        print(f"[AlertNotify] email failed: {exc}")
        return False


def dispatch_alert_notifications(conn, new_alerts: list) -> dict:
    """由告警同步线程在每轮 sync 后调用。new_alerts = sync_alerts 返回的新触发告警。"""
    if not new_alerts:
        return {"skipped": "no_new_alerts"}

    config = get_notify_config(conn)
    if not config.get("enabled"):
        return {"skipped": "disabled"}

    min_sev = _SEVERITY_ORDER.get(str(config.get("min_severity") or "critical"), 2)
    pending = [a for a in new_alerts
               if _SEVERITY_ORDER.get(str(a.get("severity") or "warning"), 1) >= min_sev]
    if not pending:
        return {"skipped": "below_min_severity"}

    hostnames = _hostname_map(conn, [a.get("asset_id") for a in pending if a.get("asset_id")])

    result = {"channel": None, "webhook": 0, "webhook_failed": 0, "webhook_error": "", "email": False, "notified": 0}
    notified_ids = []

    webhook_url = str(config.get("webhook_url") or "").strip()
    if webhook_url:
        if _is_wecom_webhook(webhook_url):
            # 企业微信群机器人：合并为一条 markdown，严格校验 errcode
            result["channel"] = "wecom"
            ok, err = _send_wecom(webhook_url, pending, hostnames)
            if ok:
                result["webhook"] = len(pending)
            else:
                result["webhook_failed"] = len(pending)
                result["webhook_error"] = err
                print(f"[AlertNotify] wecom failed: {err}")
        else:
            result["channel"] = "generic"
            for alert in pending:
                hostname = hostnames.get(alert.get("asset_id")) or str(alert.get("asset_id"))
                payload = {
                    "asset_id": alert.get("asset_id"),
                    "hostname": hostname,
                    "alert_type": alert.get("alert_type"),
                    "severity": alert.get("severity"),
                    "message": alert.get("message"),
                    "current_value": float(alert["current_value"]) if alert.get("current_value") is not None else None,
                    "threshold_value": float(alert["threshold_value"]) if alert.get("threshold_value") is not None else None,
                    "fingerprint": alert.get("active_fingerprint"),
                    "first_triggered_at": str(alert.get("first_triggered_at") or ""),
                }
                if _send_webhook(webhook_url, payload):
                    result["webhook"] += 1
                else:
                    result["webhook_failed"] += 1
        notified_ids.extend(a.get("id") for a in pending)

    result["email"] = _send_email(
        config,
        f"Z-View 告警：{len(pending)} 条新告警（>= {config.get('min_severity')}）",
        "\n\n".join(
            f"[{a.get('severity')}] {hostnames.get(a.get('asset_id')) or a.get('asset_id')} — "
            f"{a.get('message')}" for a in pending
        ),
    )

    mark_alerts_notified(conn, [i for i in notified_ids if i])
    result["notified"] = len(notified_ids)
    return result


def send_test_notification(conn) -> dict:
    """按已保存配置逐通道发送测试通知（控制台"发送测试"按钮）。

    返回每通道结果：webhook/wecom/email 各自 ok 与错误详情。
    """
    config = get_notify_config(conn)
    if not config.get("enabled"):
        return {"enabled": False, "error": "通知未启用，请先打开启用开关并保存"}
    if not config.get("webhook_url") and not config.get("smtp_host"):
        return {"enabled": True, "error": "未配置任何通道（webhook_url 与 smtp_host 均为空）"}

    test_alert = {
        "asset_id": 0,
        "alert_type": "test",
        "severity": "info",
        "message": "Z-View 测试通知：通知链路验证（可安全忽略）",
        "current_value": None,
        "threshold_value": None,
        "active_fingerprint": "test:notify",
        "first_triggered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    hostnames = {0: "TEST"}
    result: dict = {"enabled": True, "channel": None}

    webhook_url = str(config.get("webhook_url") or "").strip()
    if webhook_url:
        if _is_wecom_webhook(webhook_url):
            result["channel"] = "wecom"
            ok, err = _send_wecom(webhook_url, [test_alert], hostnames)
            result["wecom_ok"] = ok
            result["wecom_error"] = err
        else:
            result["channel"] = "webhook"
            result["webhook_ok"] = _send_webhook(webhook_url, {
                "alert_type": "test",
                "severity": "info",
                "message": test_alert["message"],
                "hostname": "TEST",
            })

    if config.get("smtp_host") and config.get("to_addrs"):
        result["email"] = _send_email(
            config,
            "Z-View 测试通知",
            "这是一条 Z-View 告警通知测试邮件（可安全忽略）。\n\n"
            f"时间：{test_alert['first_triggered_at']}",
        )
        result["email_ok"] = result["email"]

    return result
