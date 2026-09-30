[app]

# --- основное ---
title = Блоб-Скейт
package.name = blobskate
package.domain = org.blobskate
source.dir = .
# ttf и wav добавлены к обычному списку — иначе buildozer не включит шрифт и звук в APK
source.include_exts = py,ttf,wav
source.include_patterns = fonts/*,audio/*
version = 1.0

# pygame здесь — рецепт python-for-android (SDL2-сборка), тянет sdl2/sdl2_image/sdl2_mixer/sdl2_ttf сам
requirements = python3,pygame

# игра рисуется в 1280x720 (16:9) — обязательно landscape
orientation = landscape
fullscreen = 1

# разрешения не нужны: игра полностью офлайн, ничего не пишет вне своей папки
android.permissions =

android.api = 34
android.minapi = 21
android.ndk_api = 21
# arm64-v8a — все смартфоны последних ~8 лет; armeabi-v7a — совсем старые/бюджетные
android.archs = arm64-v8a,armeabi-v7a

# ветка python-for-android; если сборка упадёт на свежем master, укажите здесь
# конкретный тег/коммит (см. README.md, раздел «Если сборка не удалась»)
p4a.branch = master


[buildozer]
log_level = 2
warn_on_root = 1
