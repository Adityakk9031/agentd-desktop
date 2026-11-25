"""Look-alike web addresses — warn when a site imitates a well-known one.

"Following the Agent" (Agentrail Research, 2026) saw agents that were not given
an exact address land on look-alike domains while searching for a product. A
person might notice "amaz0n-deals.shop"; an agent reading page text may not. So
every page the agent opens is checked, and a look-alike gets a warning on the
page listing and on any payment approval. The user decides; nothing is blocked
here, because a warning that is wrong must not stop real work.

It catches the common tricks: a brand inside another domain ("paypal-secure.com",
"paypal.com.login-help.net"), one letter changed or swapped ("amazom", "gooogle"),
look-alike characters ("micr0soft", "rn" for "m"), and punycode lookalikes ("xn--").
"""

import re
from typing import Optional
from urllib.parse import urlparse

OFFICIAL = {
    "amazon": ("amazon.com", "amazon.in", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.ca", "amazon.co.jp",
               "amazon.com.au", "amazon.it", "amazon.es", "amazon.ae", "amazonaws.com"),
    "flipkart": ("flipkart.com",), "paypal": ("paypal.com", "paypal.me"), "google": ("google.com", "google.co.in", "goo.gl"),
    "microsoft": ("microsoft.com", "microsoftonline.com", "msn.com", "bing.com"), "apple": ("apple.com", "icloud.com"), "netflix": ("netflix.com",),
    "facebook": ("facebook.com", "fb.com"), "instagram": ("instagram.com",), "linkedin": ("linkedin.com",),
    "github": ("github.com", "github.io"), "twitter": ("twitter.com", "x.com"), "whatsapp": ("whatsapp.com",),
    "telegram": ("telegram.org", "t.me"), "outlook": ("outlook.com", "live.com", "office.com"),
    "dropbox": ("dropbox.com",), "ebay": ("ebay.com", "ebay.in", "ebay.co.uk"), "walmart": ("walmart.com",),
    "booking": ("booking.com",), "airbnb": ("airbnb.com", "airbnb.co.in"), "myntra": ("myntra.com",),
    "paytm": ("paytm.com",), "phonepe": ("phonepe.com",), "irctc": ("irctc.co.in",), "hdfcbank": ("hdfcbank.com",),
    "icicibank": ("icicibank.com",), "onlinesbi": ("onlinesbi.sbi", "sbi.co.in"), "chase": ("chase.com",),
    "wellsfargo": ("wellsfargo.com",), "bankofamerica": ("bankofamerica.com",), "coinbase": ("coinbase.com",),
    "binance": ("binance.com",), "steamcommunity": ("steamcommunity.com", "steampowered.com"),
    "openai": ("openai.com", "chatgpt.com"), "anthropic": ("anthropic.com", "claude.ai"),
}
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}
_LOOKS_LIKE = [("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("3", "e"), ("5", "s"), ("7", "t"), ("@", "a")]


def registrable(host: str) -> str:
    """example.co.uk from www.shop.example.co.uk (good enough for the domains we check)."""
    parts = [p for p in host.lower().strip(".").split(".") if p]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _plain(label: str) -> str:
    for fake, real in _LOOKS_LIKE:
        label = label.replace(fake, real)
    return label


def _one_edit(a: str, b: str) -> bool:
    """True if a and b differ by one insert, delete, change or swap of neighbours."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _foreign_skeleton(label: str) -> Optional[str]:
    """For a punycode label (xn--...), the name with each non-English letter as '?', e.g. 'p?ypal'."""
    try:
        decoded = label.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return None
    decoded = "".join(_CONFUSABLE.get(c, c) for c in decoded)
    return "".join(c if c.isascii() else "?" for c in decoded)


# Cyrillic and Greek letters drawn like English ones, the usual material for look-alike names
_CONFUSABLE = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "ӏ": "l", "і": "i", "ѕ": "s",
               "ј": "j", "ԁ": "d", "ɡ": "g", "ո": "n", "һ": "h", "ԛ": "q", "ԝ": "w", "ο": "o", "α": "a", "ν": "v"}


def _same_shape(skeleton: str, brand: str) -> bool:
    """Same length, and every letter either the brand's own or an unknown foreign one (at least one letter must match)."""
    return (len(skeleton) == len(brand) and any(c == b for c, b in zip(skeleton, brand))
            and all(c in ("?", b) for c, b in zip(skeleton, brand)))


def check(url_or_host: str) -> Optional[str]:
    """A warning if the address imitates a well-known site, else None."""
    host = urlparse(url_or_host).hostname if "//" in url_or_host else url_or_host
    host = (host or "").lower().strip(".")
    if not host or re.fullmatch(r"[\d.:]+", host):
        return None
    domain = registrable(host)
    for brand, officials in OFFICIAL.items():
        if any(domain == o or host == o or host.endswith("." + o) for o in officials):
            return None  # the real site
    label = domain.split(".")[0]
    plain = _plain(label)
    tokens = re.split(r"[-_]", plain)
    subdomain_labels = host[: -len(domain)].strip(".").split(".") if host != domain else []
    for brand, officials in OFFICIAL.items():
        real = officials[0]
        skeleton = _foreign_skeleton(label) if label.startswith("xn--") else None
        if skeleton and _same_shape(skeleton, brand):
            reason = "uses letters from another alphabet that look the same"
        elif plain == brand and label != brand:
            reason = "swaps letters for look-alike characters"
        elif plain != brand and brand in tokens:
            reason = f"puts '{brand}' inside another name"
        elif len(brand) >= 6 and _one_edit(plain, brand):  # short names have too many real neighbours
            reason = "is one letter away from it"
        elif brand in subdomain_labels or any(o.split(".")[0] == s for o in officials for s in subdomain_labels if s == brand):
            reason = f"only puts '{brand}' in front of another site ({domain})"
        else:
            continue
        return (f"Warning: {host} looks like an imitation of {real}: it {reason}. "
                f"It is not {real}. Do not sign in, pay or enter personal details here unless the user confirms it is the right site.")
    return None
