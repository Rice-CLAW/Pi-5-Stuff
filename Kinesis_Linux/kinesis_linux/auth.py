"""Meta sign-in and ownership HTTP translated from MetaAuth/MetaPairClient.swift.

Login uses an ephemeral WebKitGTK window on Meta's own site, never a password form
owned by this application. Undocumented endpoints can change.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import uuid
from urllib.parse import parse_qs, quote, urlparse
import requests

FRL = "FRL|388177446008673|083800dd7efbbd42eab18c9886d79c18"
AR = "AR|306760944872162|a919421a55a8ea18080ab2f10f57f1be"
HW = "HW|1312539125771114|98588f106d5d542adbf590619ca071fe"


def post(url, data, multipart=False, headers=None):
    headers = dict(headers or {})
    headers["Accept"] = "application/json"
    kwargs = {"files": {k: (None, str(v)) for k, v in data.items()}} if multipart else {"data": data}
    if multipart:
        headers["Origin"] = "https://auth.meta.com"
    try:
        r = requests.post(url, headers=headers, timeout=30, allow_redirects=False, **kwargs)
        if r.status_code != 200:
            raise ValueError(f"Meta HTTP {r.status_code}; sign in again for expired sessions")
        result = r.json()
    except requests.RequestException:
        raise ValueError("Meta request failed (connection or TLS error)") from None
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("Meta rejected the request; endpoints or account session may have changed")
    return result


def multipart(url, data):
    lsd = "S0." + "".join(str(secrets.randbelow(10)) for _ in range(6))
    return post(url, dict(data, lsd=lsd, jazoest="2" + str(sum(map(ord, lsd)))), multipart=True)


def is_login_callback(parsed):
    if parsed.scheme == "fb-viewapp":
        return parsed.netloc == "frl_login"
    if parsed.scheme == "oculus":
        return (parsed.netloc, parsed.path.rstrip("/")) in {
            ("link", "/frl_login"),
            ("frl_login", ""),
        }
    return False


def callback_values(url, native):
    parsed = urlparse(url)
    if not is_login_callback(parsed):
        raise ValueError("unexpected sign-in callback")
    query = parse_qs(parsed.query)
    token, blob = query.get("token", [""])[0], query.get("blob", [""])[0]
    expected = hashlib.sha256(native.encode()).hexdigest()[:16]
    if not hmac.compare_digest(token, expected) or not blob:
        raise ValueError("Meta callback did not match this login")
    return blob


def login():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2
    except (ImportError, ValueError):
        raise ValueError("Login needs WebKitGTK: install python3-gi gir1.2-webkit2-4.1 and use a --system-site-packages venv") from None
    tracing = os.environ.get("KINESIS_LOGIN_TRACE") == "1"

    def trace(message):
        if tracing:
            print(f"[Meta login] {message}", file=sys.stderr, flush=True)

    tokens = multipart("https://meta.graph.meta.com/webview_tokens_query", {"access_token": FRL})
    native, etoken = tokens["native_sso_token"], tokens["native_sso_etoken"]
    if not native or not etoken:
        raise ValueError("Meta did not return sign-in tokens")
    result = {}
    window = Gtk.Window(title="Sign in at auth.meta.com — Kinesis Linux")
    window.set_default_size(680, 700)
    web = WebKit2.WebView.new_with_context(WebKit2.WebContext.new_ephemeral())
    # Sign-in providers can open a popup after an asynchronous click handler.
    web.get_settings().set_javascript_can_open_windows_automatically(True)
    window.add(web)
    popups = set()

    def finish():
        for popup in tuple(popups):
            popup.destroy()
        Gtk.main_quit()

    def policy(view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            response = decision.get_response()
            trace(f"document response HTTP {response.get_status_code()}")
            return False
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                        WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        url = decision.get_navigation_action().get_request().get_uri()
        parsed = urlparse(url)
        if is_login_callback(parsed):
            decision.ignore()
            trace("received sign-in callback")
            try:
                result["blob"] = callback_values(url, native)
            except ValueError:
                result["error"] = "Meta callback failed validation"
            window.destroy()
            return True
        if parsed.scheme not in ("https", "about"):
            trace("blocked unsupported navigation scheme")
            decision.ignore()
            return True
        if parsed.hostname:
            view.get_toplevel().set_title(f"Sign in at {parsed.hostname} — Kinesis Linux")
        target = "new window" if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION else "current window"
        trace(f"navigation in {target}")
        return False

    def create_popup(parent, _action):
        trace("creating sign-in popup")
        popup = Gtk.Window(title="Continue Meta sign-in — Kinesis Linux")
        popup.set_default_size(680, 700)
        popup.set_transient_for(parent.get_toplevel())
        child = WebKit2.WebView.new_with_related_view(parent)
        popup.add(child)
        popups.add(popup)
        popup.connect("destroy", lambda *_: popups.discard(popup))
        def show_popup(_view):
            trace("showing sign-in popup")
            popup.show_all()

        child.connect("ready-to-show", show_popup)
        child.connect("close", lambda *_: popup.destroy())
        configure_view(child)
        return child

    def configure_view(view):
        view.connect("decide-policy", policy)
        view.connect("create", create_popup)
        if tracing:
            def load_failed(_view, _event, _uri, error):
                trace(f"page load failed (code {error.code})")
                return False

            def resource_started(_view, resource, _request):
                def finished(resource):
                    response = resource.get_response()
                    if response and response.get_status_code() >= 400:
                        trace(f"page resource HTTP {response.get_status_code()}")

                resource.connect("finished", finished)

            view.connect("load-failed", load_failed)
            view.connect("resource-load-started", resource_started)
            view.connect("web-process-terminated", lambda *_: trace("WebKit process terminated"))

    configure_view(web)
    window.connect("destroy", lambda *_: finish())
    web.load_uri("https://auth.meta.com/?native_app_id=388177446008673&source_app_id=388177446008673&native_sso_etoken=" + quote(etoken, safe=""))
    window.show_all()
    Gtk.main()
    if "blob" not in result:
        raise ValueError(result.get("error", "Sign-in cancelled"))
    trace("exchanging validated callback for a Meta session")
    frl = multipart("https://meta.graph.meta.com/webview_blobs_decrypt", {"blob": result["blob"], "request_token": native, "access_token": FRL})["access_token"]
    session = post("https://ar-genai.graph.meta.com/login", {
        "frl_access_token": frl, "logging_session_id": str(uuid.uuid4()), "format": "json", "device_id": str(uuid.uuid4()),
        "generate_session_cookies": "1", "generate_analytics_claim": "1", "method": "POST"}, headers={"Authorization": "OAuth " + AR})
    if not session.get("access_token"):
        raise ValueError("Meta did not return an account session")
    return {"accessToken": session["access_token"], "userID": str(session["user_id"])}


def claim(event, values, session):
    b64 = lambda data: base64.b64encode(data).decode()
    common = {"access_token": HW, "user_access_token": session["accessToken"], "user_token_universe": "ar", "pair_protocol_version": "3"}
    if event == "pair_request":
        additional = json.dumps({"device_nonce": b64(values["nonce"]), "app_pubkey": b64(values["app_public"]), "secondary_cert": b64(values["secondary"])}, separators=(",", ":"))
        params = dict(common, device_cert=b64(values["certificate"]), serial_number=values["serial"], additional_data=additional)
        reply = post("https://graph.facebook-hardware.com/pair_request", params)
        receipt = reply["pending_ownership_receipt"]
        key = None
    else:
        params = dict(common, device_pending_ownership_receipt=values["receipt"], device_pending_ownership_receipt_signature=b64(values["signature"]))
        reply = post("https://graph.facebook-hardware.com/pair", params)
        receipt = reply["final_ownership_receipt"]
        def device_key(obj):
            additional = obj.get("additional_data", {})
            return additional.get("device_ec_public_key") if isinstance(additional, dict) else None
        encoded = device_key(reply)
        if not encoded:
            encoded = device_key(json.loads(receipt))
        key = base64.b64decode(encoded, validate=True) if encoded else None
    return base64.b64decode(reply["receipt_signature"], validate=True), receipt, key
