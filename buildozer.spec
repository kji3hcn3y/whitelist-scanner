[app]

title = CIDR Pinger
package.name = cidrpinger
package.domain = org.cidrpinger

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

requirements = python3,kivy,android

# Android
android.api = 33
android.minapi = 26
android.ndk = 25b
android.accept_sdk_license = True

# Permissions
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# Python-for-Android
p4a.branch = develop

# Screen
orientation = portrait
fullscreen = 0

[buildozer]

log_level = 2
warn_on_root = 1
