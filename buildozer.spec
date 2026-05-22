[app]
title = CIDR Pinger
package.name = cidrpinger
package.domain = org.cidrpinger
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0
requirements = python3,kivy,android

# Android specific
android.permissions = INTERNET, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 26
android.ndk = 25b
android.accept_sdk_license = True

# Raw socket (ICMP) — нет root, но нужен CAP_NET_RAW
# Работает с API 29+ без root благодаря непривилегированным ICMP-сокетам
android.uses_library =

orientation = portrait
fullscreen = 0

[buildozer]
log_level = 2
warn_on_root = 1
