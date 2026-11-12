"""Press Preview on the Web Export HDA and report whether the server came up.

Run under hython with a private port and no browser, so it never touches port 5180:

    WEBEXPORT_PORT=5187 WEBEXPORT_NO_BROWSER=1 hython -B tests/hou/preview_probe.py

It prints JSON and leaves the server running; the caller stops it by PID.
"""
import json
import os
import socket
import time

import hou

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = int(os.environ.get("WEBEXPORT_PORT", "5187"))


def main():
    hou.hda.installFile(os.path.join(REPO, "houdini", "otls", "web_export.hda"), force_use_assets=True)
    stage = hou.node("/stage")
    net = stage if isinstance(stage, hou.LopNetwork) else hou.node("/obj").createNode("lopnet", "p", exact_type_name=True)
    inst = net.createNode("mythandlogic::web_export::1.0", "web_export", exact_type_name=True)
    inst.parm("preview").pressButton()
    time.sleep(2.0)
    listening = False
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", PORT))
        listening = True
    except OSError:
        listening = False
    finally:
        s.close()
    print(json.dumps({"houdini": hou.applicationVersionString(), "port": PORT, "listening": listening,
                      "report": inst.parm("report").unexpandedString()}))


if __name__ == "__main__":
    main()
