def test_format_decision():
    from src.cli.approval import format_decision_for_approval

    text = format_decision_for_approval(
        action="open_long",
        reasoning="Bullish trend",
        position_pct=20.0,
        leverage=3,
        stop_loss=63000.0,
        take_profit=68000.0,
    )
    assert "LONG" in text.upper()
    assert "63000" in text
    assert "68000" in text


def test_auto_approve_when_disabled():
    from src.cli.approval import ApprovalGate

    gate = ApprovalGate(enabled=False, timeout_seconds=300)
    result = gate.check_sync("open_long", "Bullish", 20.0, 3)
    assert result is True


def test_approval_accepted(monkeypatch):
    from src.cli.approval import ApprovalGate

    monkeypatch.setattr("builtins.input", lambda _: "y")
    gate = ApprovalGate(enabled=True, timeout_seconds=300)
    result = gate.check_sync("open_long", "Bullish trend", 20.0, 3)
    assert result is True


def test_approval_rejected(monkeypatch):
    from src.cli.approval import ApprovalGate

    monkeypatch.setattr("builtins.input", lambda _: "n")
    gate = ApprovalGate(enabled=True, timeout_seconds=300)
    result = gate.check_sync("open_long", "Weak signal", 20.0, 3)
    assert result is False
