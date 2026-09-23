from Capture.desktop_capture import DesktopFrameCapturer


def test_backend_preference_reorders_without_removing_fallbacks():
    capturer = DesktopFrameCapturer.__new__(DesktopFrameCapturer)
    capturer._preferred_backend = ""
    capturer._strategy_backend_order = ("dxgi", "wgc", "dwm", "mss", "gdi")
    capturer.backend_order = capturer._strategy_backend_order
    capturer.capture_backend = None
    order = ("dxgi", "wgc", "dwm", "mss", "gdi")

    assert capturer.set_preferred_backend("wgc") is True
    assert capturer._apply_backend_preference(order) == ("wgc", "dxgi", "dwm", "mss", "gdi")
    assert capturer.set_preferred_backend("wgc") is False


def test_auto_restores_strategy_and_invalid_value_is_rejected():
    capturer = DesktopFrameCapturer.__new__(DesktopFrameCapturer)
    capturer._preferred_backend = "dxgi"
    order = ("dxgi", "wgc", "mss")
    capturer._strategy_backend_order = order
    capturer.backend_order = order
    capturer.capture_backend = None

    assert capturer.set_preferred_backend("auto") is True
    assert capturer._apply_backend_preference(order) == order
    try:
        capturer.set_preferred_backend("gdi")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported preference must be rejected")
