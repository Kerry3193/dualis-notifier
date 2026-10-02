"""One authenticated Requests session with bounded connect/read waits."""
import re

import requests

URL = "https://dualis.dhbw.de/scripts/mgrqispi.dll"


class DualisError(Exception):
    pass


class DualisClient:
    def __init__(self, user_agent="Dualis Notifier", timeout=(5, 30)):
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.session_id = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def _request(self, method, **kwargs):
        try:
            response = self.http.request(method, URL, timeout=self.timeout,
                                         allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise DualisError("Dualis ist derzeit nicht erreichbar.") from exc
        if response.status_code != 200:
            status = response.status_code
            response.close()
            raise DualisError(f"Dualis-Anfrage fehlgeschlagen (HTTP {status}).")
        return response

    def login(self, user: str, password: str) -> None:
        payload = {
            "usrname": f"{user}@student.dhbw-mannheim.de", "pass": password,
            "APPNAME": "CampusNet", "PRGNAME": "LOGINCHECK",
            "ARGUMENTS": "clino,usrname,pass,menuno,menu_type,browser,platform",
            "clino": "000000000000001", "menuno": "000324", "menu_type": "classic",
            "browser": "", "platform": "",
        }
        self.session_id = None
        self.http.cookies.clear()
        with self._request("POST", data=payload) as response:
            refresh = response.headers.get("Refresh", "")
            match = re.search(r"[?&]ARGUMENTS=(-N\d+|\d+),", refresh, re.IGNORECASE)
            if not match or not self.http.cookies:
                raise DualisError("Dualis-Anmeldung fehlgeschlagen; Zugangsdaten oder Antwortformat prüfen.")
            self.session_id = match.group(1)

    def get_grades(self, semester_id: str) -> str:
        if self.session_id is None:
            raise DualisError("Vor dem Notenabruf ist eine Anmeldung erforderlich.")
        with self._request("GET", params={
            "APPNAME": "CampusNet", "PRGNAME": "COURSERESULTS",
            "ARGUMENTS": f"{self.session_id},-N000307,{semester_id}",
        }) as response:
            return response.text
