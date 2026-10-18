"""Exercise first-use HTTP routes against a running loopback app."""
from __future__ import annotations

import argparse
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("use a loopback test server")

    def request(path, *, data=None, headers=None):
        with urlopen(Request(base + path, data=data, headers=headers or {}), timeout=15) as response:
            return response.read(), response.headers, response.url

    def status(path, expected, **kwargs):
        try:
            request(path, **kwargs)
        except HTTPError as error:
            assert error.code == expected, (path, error.code)
        else:
            raise AssertionError(f"{path} should return {expected}")

    home = request("/")[0].decode()
    token = re.search(r'name="csrf_token" value="([^"]+)"', home).group(1)
    assert "Explore an example" in home
    case_body = request("/case-study")[0].decode()
    assert "RD101" in case_body and "fanuc_hardware_interface" in case_body
    case = json.loads(request("/case-study/case.json")[0])
    assert case["target_before"] == 1 and case["target_after"] == 0
    assert request("/case-study/before.html")[0]
    assert request("/case-study/after.html")[0]
    assert b"+  <exec_depend>fanuc_hardware_interface</exec_depend>" in request("/case-study/fix.patch")[0]
    assert request("/case-study/licenses/Apache-2.0.txt")[0]
    status("/case-study/../SECURITY.md", 404)
    for stage in ("broken", "fixed"):
        body = request(f"/example?stage={stage}")[0].decode()
        result_path = re.search(r'href="(/tasks/[^/]+/result.json)"', body).group(1)
        data = json.loads(request(result_path)[0])
        assert [item["code"] for item in data["diagnostics"]] == (["RD302"] if stage == "broken" else [])
        for filename in ("result.html", "basic.md", "intermediate.md", "expert.md"):
            assert request(result_path.replace("result.json", filename))[0]
        filtered = request(result_path.replace("/result.json", "?severity=warning"))[0].decode()
        if stage == "broken":
            assert "No findings match these filters" in filtered

    archive = request("/example/broken.zip")[0]
    boundary = "robot-doctor-smoke-boundary"

    def upload(extra_git=False):
        fields = {"csrf_token": token}
        if extra_git:
            fields["git_url"] = "https://github.com/example/not-contacted.git"
        chunks = []
        for key, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="repository_zip"; filename="pilot.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode() + archive + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks)

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": base}
    _, _, task_url = request("/scan", data=upload(), headers=headers)
    task_path = urlparse(task_url).path
    for _ in range(50):
        try:
            data = json.loads(request(task_path + "/result.json")[0])
            break
        except HTTPError as error:
            if error.code != 404:
                raise
            time.sleep(0.1)
    else:
        raise AssertionError("upload scan did not finish")
    assert data["repository"]["source"] == "pilot.zip"
    assert [item["code"] for item in data["diagnostics"]] == ["RD302"]
    assert "pilot.zip" in request(task_path)[0].decode()
    status("/scan", 400, data=upload(extra_git=True), headers=headers)
    status("/scan", 400, data=urlencode({"git_url": "https://github.com/example/not-contacted.git"}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    status("/", 403, headers={"Host": "untrusted.example"})
    status("/example?stage=unknown", 404)
    status("/example/private.zip", 404)
    print("PASS first-use HTTP: public issue case, offline examples, downloads, ZIP scan, filters, project title, ambiguous input, CSRF, Host, and invalid-stage checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
