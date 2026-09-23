# -*- coding: utf-8 -*-
"""域机远控冒烟实测（目标可为任意资产，全部断言协议级/帧差分级）。

场景:
  T1 同意流程 + screen_info 合理性
  T2 帧流
  T3 远程打开记事本(开始菜单注入) -> 左键聚焦 -> 键盘输入 -> Ctrl+A/C
     -> 剪贴板协议回读 == 输入内容   （同时证明: 键盘注入/左键聚焦/热键/剪贴板通道）
  T4 文本选区拖动(帧内坐标): 按住拖过已输入行 -> Ctrl+C 回读为部分文本  （证明左键拖动）
  T5 右键菜单出现/关闭（点击点周边像素差分检测）

用法:
  python tests\\remote_domain_smoke_test.py <asset_id> [--skip-uac]
注意: 需要人工在域机屏幕上点击一次「允 许」；测试会在域机上打开记事本。
"""

import asyncio
import base64
import io
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import websockets  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


async def recv_until(ws, wanted_types, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
        message = json.loads(raw)
        if message.get("type") in wanted_types:
            return message
    raise TimeoutError(f"等待 {wanted_types} 超时")


async def send_and_settle(ws, payload, settle=0.35):
    await ws.send(json.dumps(payload))
    await asyncio.sleep(settle)
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.01)
            del raw
    except asyncio.TimeoutError:
        pass


async def grab_frame(ws, timeout=4.0):
    """抓一帧并解码为 PIL 图像。"""
    from PIL import Image

    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - time.time()))
        message = json.loads(raw)
        if message.get("type") == "frame" and message.get("data"):
            image = Image.open(io.BytesIO(base64.b64decode(message["data"]))).convert("RGB")
            return image, (message.get("width"), message.get("height"))
    raise TimeoutError("frame wait timeout")


async def region_diff(ws, fx, fy, radius=0.06, settle=1.0):
    """对归一化点位附近区域做前后帧差分，返回平均像素差。"""
    import numpy as np

    before, _size = await grab_frame(ws)
    await asyncio.sleep(settle)
    after, _size = await grab_frame(ws)
    w, h = before.size
    box = (
        max(0, int((fx - radius) * w)), max(0, int((fy - radius) * h)),
        min(w, int((fx + radius) * w)), min(h, int((fy + radius) * h)),
    )
    a = np.asarray(before.crop(box), dtype=np.int16)
    b = np.asarray(after.crop(box), dtype=np.int16)
    return float(np.abs(a - b).mean()), before, after


async def key_press(ws, key, settle=0.35, **modifiers):
    await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": key, **modifiers}, settle)


async def click(ws, fx, fy, button=0, settle=0.6):
    await send_and_settle(ws, {"type": "mouse", "action": "move",
                               "normalized_x": fx, "normalized_y": fy}, 0.25)
    await send_and_settle(ws, {"type": "mouse", "action": "button_down", "button": button,
                               "normalized_x": fx, "normalized_y": fy}, 0.12)
    await send_and_settle(ws, {"type": "mouse", "action": "button_up", "button": button,
                               "normalized_x": fx, "normalized_y": fy}, settle)


async def clipboard_get(ws, timeout=10):
    await ws.send(json.dumps({"type": "clipboard_get"}))
    reply = await recv_until(ws, {"clipboard_data"}, timeout=timeout)
    return str(reply.get("text") or "")


async def run_test():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asset_id = int(args[0]) if args else 28
    skip_uac = "--skip-uac" in sys.argv

    from auth_utils import issue_access_token
    token = issue_access_token("admin")["access_token"]
    uri = f"ws://127.0.0.1:8080/api/v1/assets/{asset_id}/remote-desktop/ws"
    print(f"[env] 目标资产: {asset_id}", flush=True)

    async with websockets.connect(uri, max_size=None, ping_interval=None) as ws:
        print(">>> 请在域机屏幕上点击弹窗中的「允 许」（90 秒内），之后勿动键鼠 <<<", flush=True)

        consent_required = await recv_until(ws, {"consent_required"})
        record("T1-a consent_required", consent_required.get("target") is not None)

        consent_result = await recv_until(ws, {"consent_result"}, timeout=130)
        approved = consent_result.get("approved") is True
        record("T1-b consent_result approved", approved,
               f"reason={consent_result.get('reason')}")
        if not approved:
            return False
        await asyncio.sleep(2.0)

        screen_info = await recv_until(ws, {"screen_info"}, timeout=15)
        si_w, si_h = int(screen_info.get("width") or 0), int(screen_info.get("height") or 0)
        record("T1-c screen_info 合理性", si_w >= 800 and si_h >= 600, f"{si_w}x{si_h}")

        frame_count = 0
        deadline = time.time() + 4.0
        first_frame = None
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("type") == "frame":
                frame_count += 1
                if first_frame is None and msg.get("data"):
                    from PIL import Image
                    first_frame = Image.open(io.BytesIO(base64.b64decode(msg["data"]))).convert("RGB")
        record("T2 帧流接收", frame_count >= 5, f"{frame_count} 帧/4s")
        if first_frame is None:
            return False

        # ---- T3 键盘输入 + 复制闭环（远程无本地状态依赖） ----
        await key_press(ws, "win", settle=1.2)
        for ch in "notepad":
            await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": ch}, 0.12)
        await key_press(ws, "enter", settle=2.8)
        marker = "zview-domain-ok"
        await click(ws, 0.5, 0.55, settle=0.9)
        await key_press(ws, "end", settle=0.4)
        for ch in marker:
            await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": ch}, 0.1)
        await asyncio.sleep(0.6)
        await key_press(ws, "a", settle=0.4, ctrlKey=True)
        await key_press(ws, "c", settle=0.7, ctrlKey=True)
        clip = await clipboard_get(ws)
        record("T3 键盘输入+复制回读闭环", marker in clip,
               f"clip_len={len(clip)} found={marker in clip}")

        # ---- T4 文本选区拖动 ----
        # 光标移到行首(home)，拖过前半行选中，Ctrl+C 应得到 marker 的子串
        await key_press(ws, "home", settle=0.4)
        fw, fh = first_frame.size
        sx_f, sy_f = 0.42, 0.55
        ex_f, ey_f = 0.52, 0.55
        await send_and_settle(ws, {"type": "mouse", "action": "move",
                                   "normalized_x": sx_f, "normalized_y": sy_f}, 0.3)
        await send_and_settle(ws, {"type": "mouse", "action": "button_down", "button": 0,
                                   "normalized_x": sx_f, "normalized_y": sy_f}, 0.15)
        steps = 6
        for i in range(1, steps + 1):
            t = i / steps
            await send_and_settle(ws, {"type": "mouse", "action": "drag_move",
                                       "normalized_x": sx_f + (ex_f - sx_f) * t,
                                       "normalized_y": sy_f + (ey_f - sy_f) * t}, 0.14)
        await send_and_settle(ws, {"type": "mouse", "action": "button_up", "button": 0,
                                   "normalized_x": ex_f, "normalized_y": ey_f}, 0.5)
        await key_press(ws, "c", settle=0.7, ctrlKey=True)
        sel = await clipboard_get(ws)
        sel_ok = bool(sel) and set(sel.strip()) <= set(marker) and len(sel.strip()) >= 2
        record("T4 左键拖动产生文本选区", sel_ok, f"selected={sel[:24]!r}")

        # ---- T5 右键菜单出现/关闭（像素差分） ----
        mfx, mfy = 0.5, 0.62
        diff_before, _, _ = await region_diff(ws, mfx, mfy, settle=0.2)
        await click(ws, mfx, mfy, button=2, settle=1.0)
        diff_menu, _, _ = await region_diff(ws, mfx, mfy, radius=0.08, settle=0.4)
        record("T5-a 右键菜单画面变化", diff_menu > max(2.0, diff_before * 1.5),
               f"menu_diff={diff_menu:.1f} baseline={diff_before:.1f}")
        await key_press(ws, "escape", settle=0.5)
        diff_closed, _, _ = await region_diff(ws, mfx, mfy, radius=0.08, settle=0.4)
        record("T5-b ESC 关闭菜单画面还原", diff_closed < diff_menu,
               f"closed_diff={diff_closed:.1f} < menu_diff={diff_menu:.1f}")

        if not skip_uac:
            print("[UAC] 触发提权：请观察域机画面是否弹出 UAC；策略开启时可用注入点击「是」，否则手动点击。", flush=True)
            await key_press(ws, "win", settle=1.2)
            for ch in "cmd":
                await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": ch}, 0.12)
            await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": "enter",
                                       "ctrlKey": True, "shiftKey": True}, 2.5)
            print("[UAC] 已发送提权指令——此步骤的通过判据为人工确认（安全桌面画面+可否点击）。", flush=True)

        return True


async def main():
    ok = False
    try:
        ok = await run_test()
    finally:
        subprocess_cleanup()
    print("=" * 60)
    failed = RESULTS.count(False)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    return ok and failed == 0


def subprocess_cleanup():
    pass


if __name__ == "__main__":
    final_ok = asyncio.run(main())
    import os
    os._exit(0 if final_ok else 1)
