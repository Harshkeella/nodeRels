"""python -m backend.test_env -- the .env parser, no API call."""
from . import _parse, load
import os


def main():
    p = _parse(
        "# comment\r\n"
        "GROQ_API_KEY= gsk_abc123            # https://console.groq.com/keys\r\n"
        "GROQ_TPM=8000     # free tier\r\n"
        "export QUOTED=\"a b\"\r\n"
        "BLANK=      # not set yet\r\n"
        "URL=https://x.dev/#frag\r\n"
        "\r\n"
        "junk line no equals\r\n")
    assert p["GROQ_API_KEY"] == "gsk_abc123", p["GROQ_API_KEY"]   # space + comment + CR
    assert int(p["GROQ_TPM"]) == 8000
    assert p["QUOTED"] == "a b"
    assert p["BLANK"] == ""
    assert p["URL"] == "https://x.dev/#frag"          # only " #" starts a comment
    assert "junk line no equals" not in p

    os.environ["GROQ_API_KEY"] = "real"
    load()
    assert os.environ["GROQ_API_KEY"] == "real"       # env wins over the file
    print("env ok")


if __name__ == "__main__":
    main()
