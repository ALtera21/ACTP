import base64

from actp import shortcut


def test_script_escapes_quotes_and_targets_the_real_desktop():
    script = shortcut.build_script(
        r"C:\Users\O'Neil\pythonw.exe", '"C:\\a b\\run_actp.py"',
        r"C:\SACTP", r"C:\SACTP\actp_icon.ico")
    assert "GetFolderPath('Desktop')" in script
    assert "O''Neil" in script          # ' doubled inside '...'
    assert "ACTP.lnk" in script
    assert "$s.Save()" in script


def test_encoded_command_round_trips():
    script = "Write-Output 'é'"
    back = base64.b64decode(shortcut.encode(script)).decode("utf-16-le")
    assert back == script


def test_not_windows_gives_a_reason(monkeypatch):
    monkeypatch.setattr(shortcut.sys, "platform", "linux")
    assert "Windows" in shortcut.create_desktop_shortcut()
