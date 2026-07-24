"""Constants for the daelim-smarthome integration."""

from datetime import timedelta

DOMAIN = "daelim_smarthome"

API_PREFIX = "https://smartelife.apt.co.kr"

# The first request tries the pooled connection and fails fast: a socket
# silently dropped during idle should not eat the whole budget. The retry
# redials a fresh connection and waits far longer, because a cold server can
# genuinely take several seconds to answer (e.g. /common/nativeToken.ajax).
CONNECT_TIMEOUT = 5
FAST_READ_TIMEOUT = 3
READ_TIMEOUT = 15
RETRY = 3

REFRESH_INTERVAL = timedelta(minutes=10)

BS = 256 // 16
KEY = b"\x31\x32\x33\x34\x35\x36\x37\x38\x39\x30\x31\x32\x33\x34\x35\x36\x37\x38\x39\x30\x31\x32\x33\x34\x35\x36\x37\x38\x39\x30\x31\x32"
IV = b"\x48\x72\x50\x74\x48\x34\x6b\x76\x68\x4b\x6a\x56\x73\x50\x55\x3d"
