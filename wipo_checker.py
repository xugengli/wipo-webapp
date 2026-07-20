#!/usr/bin/env python3
"""
WIPO Global Brand Database risk checker — web app module.
Refactored for cross-platform deployment (local + Streamlit Cloud).
"""

import re
import json
import time
import uuid
import shutil
import subprocess
import tempfile
import os
import unicodedata
import concurrent.futures
from pathlib import Path
from typing import Optional, Callable

import requests
import altcha

BASE = "https://branddb.wipo.int"
API_BASE = "https://api.branddb.wipo.int"
DECRYPT_SCRIPT = Path(__file__).with_name("decrypt_wipo.js")

DEFAULT_OFFICES = ["US", "CA", "DE", "FR", "GB"]
DEFAULT_NICE_CLASS = "28"
DEFAULT_STATUS = "Registered"

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "are", "is", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "shall",
    "can", "need", "dare", "ought", "used", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "also", "new", "old", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "them", "their", "what",
    "which", "who", "whom", "whose", "am", "its", "our", "ours", "his",
    "her", "him", "my", "me", "us", "use", "using", "called", "call",
    "name", "named", "launch", "launching", "line", "product", "products",
    "brand", "brands", "phrase", "phrases", "word", "words", "term", "terms",
    # French stop words
    "les", "des", "une", "aux", "du", "est", "sur", "dans", "pour", "avec",
    "que", "qui", "ses", "leur", "lui", "son", "notre", "votre", "oui",
    "non", "mais", "donc", "car", "plus", "moins", "tres", "tout", "tous",
    "toute", "toutes", "rien", "autre", "autres", "meme", "aussi", "bien",
    "entre", "chez", "sans", "sous", "vers", "depuis", "pendant", "selon",
    "par", "comme", "si", "alors", "encore", "deja", "toujours", "jamais",
    "souvent", "parfois", "ici", "voici", "voila",
    # German stop words
    "der", "die", "das", "den", "dem", "ein", "eine", "einen", "einem",
    "einer", "eines", "und", "oder", "aber", "denn", "weil", "wenn", "als",
    "ob", "doch", "nicht", "kein", "keine", "auch", "noch", "schon", "immer",
    "wieder", "ganz", "viel", "viele", "mehr", "wenig", "hier", "dort",
    "wo", "was", "wer", "wie", "warum", "wann", "mit", "von", "zu", "aus",
    "bei", "nach", "seit", "gegen", "ohne", "um", "fur", "uber", "unter",
    "vor", "zwischen", "durch", "wahrend",
}


def find_node() -> str:
    """Locate node executable (cross-platform, no hardcoded paths)."""
    node = shutil.which("node") or shutil.which("nodejs")
    if node:
        return node
    candidates = [
        "/usr/bin/node",
        "/usr/local/bin/node",
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "Node.js not found. On Streamlit Cloud, add 'nodejs' to packages.txt. "
        "Locally, install from https://nodejs.org/"
    )


def find_node_modules() -> str:
    """Locate node_modules directory containing crypto-js."""
    script_dir = Path(__file__).parent
    candidates = [
        script_dir / "node_modules",
        Path.cwd() / "node_modules",
    ]
    for c in candidates:
        if (c / "crypto-js" / "package.json").is_file():
            return str(c)
    return ""


def ensure_crypto_js(log: Callable[[str], None] = print) -> None:
    """Ensure crypto-js is available (bundled copy or via npm install)."""
    script_dir = Path(__file__).parent
    # Streamlit Cloud / bundled copy: no npm needed
    if (script_dir / "crypto-js.min.js").is_file():
        return
    if find_node_modules():
        return
    log("Installing crypto-js (one-time setup)...")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError("npm not found. Please install Node.js with npm.")
    result = subprocess.run(
        [npm, "install", "crypto-js"],
        cwd=str(script_dir),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm install crypto-js failed: {result.stderr}")
    log("crypto-js installed successfully.")


class WipoClient:
    def __init__(self, log_callback: Callable[[str], None] = None):
        self.session = requests.Session()
        self._set_headers()
        self.hash_search: Optional[str] = None
        self.authenticated = False
        self.node = find_node()
        self.node_modules = find_node_modules()
        self.had_auth_error = False
        self.log = log_callback or print

    def _set_headers(self):
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

    @staticmethod
    def _extract_uuid(html: str) -> Optional[str]:
        m = re.search(r'let uuid="([^"]+)"', html)
        return m.group(1) if m else None

    @staticmethod
    def _solve_captcha(challenge: dict) -> altcha.v1.Solution:
        return altcha.solve_challenge_v1(
            challenge["challenge"],
            challenge["salt"],
            challenge["algorithm"],
            challenge["maxnumber"],
        )

    @staticmethod
    def _build_payload(challenge: dict, number: int) -> str:
        payload = altcha.v1.Payload(
            algorithm=challenge["algorithm"],
            challenge=challenge["challenge"],
            number=number,
            salt=challenge["salt"],
            signature=challenge["signature"],
        )
        return payload.to_base64()

    def authenticate(self, redirect: str = "/api/search") -> bool:
        self.session = requests.Session()
        self._set_headers()
        r = self.session.get(f"{BASE}{redirect}", timeout=30)
        html = r.text
        uid = self._extract_uuid(html)
        if not uid:
            self.log("Failed to extract uuid from anti-bot page")
            return False

        for attempt in range(5):
            cr = self.session.get(f"{API_BASE}/captcha", timeout=30)
            if cr.status_code in (429, 403):
                wait = 15 + attempt * 15
                self.log(f"  Captcha {cr.status_code}, waiting {wait}s "
                         f"(attempt {attempt+1}/5)...")
                time.sleep(wait)
                self.session = requests.Session()
                self._set_headers()
                r = self.session.get(f"{BASE}{redirect}", timeout=30)
                uid = self._extract_uuid(r.text)
                if not uid:
                    continue
                continue
            if cr.status_code != 200:
                self.log(f"Captcha error: {cr.status_code} {cr.text}")
                return False
            ch = cr.json()
            break
        else:
            return False

        self.log(f"Solving ALTCHA (maxnumber={ch['maxnumber']})...")
        t0 = time.time()
        sol = self._solve_captcha(ch)
        self.log(f"ALTCHA solved in {time.time() - t0:.2f}s")

        token = self._build_payload(ch, sol.number)
        self.hash_search = str(uuid.uuid4())

        dr = self.session.get(
            f"{API_BASE}/dbinfo",
            params={"token": token},
            headers={"HashSearch": self.hash_search},
            timeout=30,
        )
        if dr.status_code != 200:
            self.log(f"dbinfo failed: {dr.status_code} {dr.text[:200]}")
            return False

        self.session.cookies.set("session_id", uid,
                                 domain="branddb.wipo.int", path="/")
        self.session.cookies.set("session_id", uid,
                                 domain="api.branddb.wipo.int", path="/")
        self.authenticated = True
        return True

    def _decrypt(self, ciphertext: str) -> dict:
        if not DECRYPT_SCRIPT.exists():
            raise FileNotFoundError(f"Decrypt script not found: {DECRYPT_SCRIPT}")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as cf:
            cf.write(ciphertext)
            cipher_path = cf.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as hf:
            hf.write(self.hash_search or "")
            hash_path = hf.name
        out_path = cipher_path + ".out"

        try:
            env = os.environ.copy()
            if self.node_modules:
                existing = env.get("NODE_PATH", "")
                env["NODE_PATH"] = self.node_modules + (
                    os.pathsep + existing if existing else "")
            result = subprocess.run(
                [self.node, str(DECRYPT_SCRIPT), cipher_path, hash_path, out_path],
                capture_output=True, text=True, timeout=30, check=True, env=env,
            )
            plaintext = result.stdout
            if not plaintext and os.path.exists(out_path):
                plaintext = Path(out_path).read_text(encoding="utf-8")
            return json.loads(plaintext)
        finally:
            for p in (cipher_path, hash_path, out_path):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass

    def _raw_search(self, body: dict) -> requests.Response:
        return self.session.post(
            f"{API_BASE}/search",
            data=json.dumps(body),
            headers={"HashSearch": self.hash_search,
                     "Content-Type": "application/json"},
            timeout=60,
        )

    def _query_office(self, office, term, status, nice_class, rows):
        as_structure = {
            "_id": "root", "boolean": "AND",
            "bricks": [
                {"_id": "b1", "key": "brandName",
                 "strategy": "Simple", "value": term},
                {"_id": "b3", "key": "niceClass",
                 "strategy": "all_of", "value": nice_class},
                {"_id": "b4", "key": "office",
                 "strategy": "any_of", "value": office},
            ],
        }
        body = {
            "sort": "score desc", "strategy": "concept",
            "rows": rows, "start": 0,
            "asStructure": json.dumps(as_structure),
        }
        headers = dict(self.session.headers)
        headers["HashSearch"] = self.hash_search
        headers["Content-Type"] = "application/json"
        cookies = self.session.cookies.copy()
        try:
            r = requests.post(f"{API_BASE}/search", data=json.dumps(body),
                              headers=headers, cookies=cookies, timeout=60)
            if r.status_code == 401:
                return (office, None, "401")
            if r.status_code == 403:
                return (office, None, "403")
            r.raise_for_status()
            return (office, self._decrypt(r.text), None)
        except Exception as e:
            return (office, None, str(e))

    def search(self, term, status=DEFAULT_STATUS, nice_class=DEFAULT_NICE_CLASS,
               offices=None, rows=20, per_office=False):
        if not self.authenticated:
            raise RuntimeError("Not authenticated.")
        offices = offices or DEFAULT_OFFICES
        if not per_office or len(offices) == 1:
            return self._search_single(term, status, nice_class, offices, rows)

        all_docs, seen = [], set()
        remaining = list(offices)
        self.had_auth_error = False

        for attempt in range(3):
            if not remaining:
                break
            if attempt > 0:
                self.authenticated = False
                wait = 3 + (attempt - 1) * 5
                self.log(f"  Re-authenticating (attempt {attempt}/3, "
                         f"wait {wait}s)...")
                time.sleep(wait)
                if not self.authenticate():
                    self.log(f"  Re-auth failed, skipping: {remaining}")
                    break

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(5, len(remaining))
            ) as executor:
                futures = {
                    executor.submit(self._query_office, o, term,
                                    status, nice_class, rows): o
                    for o in remaining
                }
                retry = []
                for future in concurrent.futures.as_completed(futures):
                    office, data, error = future.result()
                    if error == "401":
                        retry.append(office)
                        self.had_auth_error = True
                    elif error == "403":
                        retry.append(office)
                        self.had_auth_error = True
                        self.log(f"  403 blocked on office {office}, will retry")
                    elif error:
                        self.log(f"  Warning: office {office} failed: {error}")
                    elif data:
                        for d in data.get("response", {}).get("docs", []):
                            key = (d.get("st13")
                                   or d.get("registrationNumber")
                                   or str(d.get("brandName")))
                            if key not in seen:
                                seen.add(key)
                                all_docs.append(d)
                remaining = retry

        if remaining:
            self.log(f"  Could not query offices: {remaining}")
        all_docs.sort(key=lambda d: d.get("score", 0), reverse=True)
        return {"response": {"docs": all_docs}}

    def _search_single(self, term, status=DEFAULT_STATUS,
                       nice_class=DEFAULT_NICE_CLASS, offices=None, rows=20):
        offices = offices or DEFAULT_OFFICES
        as_structure = {
            "_id": "root", "boolean": "AND",
            "bricks": [
                {"_id": "b1", "key": "brandName",
                 "strategy": "Simple", "value": term},
                {"_id": "b3", "key": "niceClass",
                 "strategy": "all_of", "value": nice_class},
                {"_id": "b4", "key": "office",
                 "strategy": "any_of", "value": ", ".join(offices)},
            ],
        }
        body = {
            "sort": "score desc", "strategy": "concept",
            "rows": rows, "start": 0,
            "asStructure": json.dumps(as_structure),
        }
        r = self._raw_search(body)
        for retry in range(3):
            if r.status_code not in (401, 403):
                break
            wait = 5 + retry * 10
            if r.status_code == 401:
                self.log(f"  Session expired (401), wait {wait}s "
                         f"(attempt {retry+1}/3)...")
                self.authenticated = False
                time.sleep(wait)
                if self.authenticate():
                    r = self._raw_search(body)
                elif retry < 2:
                    continue
                else:
                    raise RuntimeError("Re-authentication failed after 3 attempts")
            else:  # 403
                self.log(f"  Rate limited (403), wait {wait}s "
                         f"(attempt {retry+1}/3)...")
                time.sleep(wait)
                r = self._raw_search(body)
        r.raise_for_status()
        return self._decrypt(r.text)


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii")


def extract_terms(text: str, min_len: int = 3) -> list[str]:
    text = strip_accents(text)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9']*", text)
    terms = []
    for t in tokens:
        t = t.strip("'").lower()
        if len(t) >= min_len and re.fullmatch(r"[a-z]+", t) and t not in STOP_WORDS:
            terms.append(t)

    segments = re.split(r"[^a-zA-Z\s]+", text)
    phrases = []
    for seg in segments:
        seg_tokens = re.findall(r"[A-Za-z][A-Za-z0-9']*", seg)
        seg_tokens = [t.strip("'").lower() for t in seg_tokens]
        if len(seg_tokens) < 2:
            continue
        for n in range(2, min(len(seg_tokens), 4) + 1):
            for i in range(len(seg_tokens) - n + 1):
                words = seg_tokens[i : i + n]
                if all(w in STOP_WORDS for w in words):
                    continue
                phrase = " ".join(words)
                if len(phrase.replace(" ", "")) >= min_len:
                    phrases.append(phrase)

    seen, result = set(), []
    for t in terms + phrases:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def classify_match(term: str, brand_name: str) -> str:
    term, brand = term.lower(), brand_name.lower()
    if term == brand:
        return "exact"
    if re.search(r'\b' + re.escape(term) + r'\b', brand):
        return "contains"
    return "similar"


def check_text(
    text: str,
    offices: list[str] | None = None,
    nice_class: str = DEFAULT_NICE_CLASS,
    status: str = DEFAULT_STATUS,
    delay: float = 3.0,
    max_terms: Optional[int] = None,
    per_office: bool = False,
    offset: int = 0,
    limit: int = 0,
    progress_callback: Callable[[int, int, str], None] = None,
    log_callback: Callable[[str], None] = None,
) -> list[dict]:
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    ensure_crypto_js(log=log)
    client = WipoClient(log_callback=log)
    if not client.authenticate():
        raise RuntimeError("WIPO authentication failed")

    terms = extract_terms(text)
    total_terms = len(terms)
    if offset > 0 or limit > 0:
        start = offset
        end = offset + limit if limit > 0 else total_terms
        terms = terms[start:end]
    if max_terms:
        terms = terms[:max_terms]
    log(f"Extracted {total_terms} candidate terms (checking {len(terms)})")

    risks = []
    current_delay = delay
    consecutive_success = 0
    for i, term in enumerate(terms, 1):
        if progress_callback:
            progress_callback(i, len(terms), term)

        if not client.authenticated:
            log("  Session lost, re-authenticating...")
            for auth_attempt in range(3):
                time.sleep(5 + auth_attempt * 5)
                if client.authenticate():
                    break
            else:
                log(f"  Skipping {term}: re-auth failed")
                continue
        log(f"[{i}/{len(terms)}] Checking: {term}")
        had_error = False
        try:
            data = client.search(term, status=status, nice_class=nice_class,
                                 offices=offices, per_office=per_office)
            docs = data.get("response", {}).get("docs", [])
            docs = [d for d in docs if d.get("status") == status]
            exact_docs = []
            for d in docs:
                brand = d.get("brandName", [""])[0]
                if classify_match(term, brand) == "exact":
                    exact_docs.append((d, brand))
            if exact_docs:
                examples = [{
                    "brandName": brand,
                    "office": d.get("office", ""),
                    "status": d.get("status", ""),
                    "niceClass": d.get("niceClass", []),
                    "regNumber": d.get("registrationNumber", ""),
                } for d, brand in exact_docs]
                risks.append({"term": term, "hits": len(examples),
                              "examples": examples})
        except Exception as e:
            log(f"Error checking {term}: {e}")
            had_error = True

        if had_error or client.had_auth_error:
            current_delay = min(current_delay + 1.0, 8.0)
            consecutive_success = 0
        else:
            consecutive_success += 1
            if consecutive_success >= 10 and current_delay > delay:
                current_delay = max(current_delay - 0.5, delay)
                consecutive_success = 0
                log(f"  Delay reduced to {current_delay}s")

        if current_delay:
            time.sleep(current_delay)
    return risks


def generate_report(text, risks, offices, nice_class, status) -> str:
    lines = [
        "# WIPO 商标风险排查报告", "",
        f"- 排查办公室：{', '.join(offices)}",
        f"- Nice 分类：{nice_class}",
        f"- 状态筛选：{status}",
        f"- 候选词数：{len(extract_terms(text))}",
        f"- 风险词数：{len(risks)}", "",
    ]
    if not risks:
        lines.append("未发现明显风险词汇。")
        return "\n".join(lines)
    lines += ["## 风险词汇列表", ""]
    for item in risks:
        lines += [f"### `{item['term']}` — 命中 {item['hits']} 件", "",
                   "| 商标名称 | 办公室 | 注册号 |", "|---|---|---|"]
        for ex in item["examples"]:
            reg = ex.get("regNumber") or "—"
            lines.append(f"| {ex['brandName']} | {ex['office']} | {reg} |")
        lines.append("")
    return "\n".join(lines)
