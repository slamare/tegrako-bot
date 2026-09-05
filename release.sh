#!/usr/bin/env bash
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; RED="\033[31m"; RESET="\033[0m"
info() { echo -e "${GREEN}==>${RESET} $1"; }
fail() { echo -e "${RED}xx${RESET} $1"; exit 1; }

[ $# -eq 1 ] || fail "Использование: ./release.sh v1.5.0"
TAG="$1"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Тег должен быть вида v1.5.0"

REPO="slamare/tegrako-bot"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
[ -n "$GH_TOKEN" ] || fail "Задай GH_TOKEN (короткоживущий PAT с правами repo) в переменной окружения."

command -v git >/dev/null || fail "git не найден"
command -v curl >/dev/null || fail "curl не найден"

[ -f VERSION ] || fail "Запусти из корня репозитория (VERSION не найден)."

git diff --quiet && git diff --cached --quiet || fail "Есть незакоммиченные изменения — закоммить или застэшь перед релизом."

CURRENT_VER=$(cat VERSION)
NEW_VER=$((CURRENT_VER + 1))
info "VERSION: ${CURRENT_VER} → ${NEW_VER}"

echo "$NEW_VER" > VERSION
sed -i "s/^INSTALL_SH_VERSION=.*/INSTALL_SH_VERSION=${NEW_VER}/" install.sh

git add VERSION install.sh
git commit -m "release ${TAG}: VERSION → ${NEW_VER}"
git push origin HEAD

git tag -f "$TAG"
git push -f origin "refs/tags/${TAG}"

git tag -f stable
git push -f origin refs/tags/stable

info "Теги ${TAG} и stable запушены."

API="https://api.github.com/repos/${REPO}"
AUTH_HEADER="Authorization: token ${GH_TOKEN}"

release_id=$(curl -s -H "$AUTH_HEADER" "${API}/releases/tags/${TAG}" | grep -m1 '"id"' | grep -o '[0-9]*' || true)

if [ -n "${release_id:-}" ]; then
    info "Релиз ${TAG} уже существует (id=${release_id}), обновляю ассеты."
else
    info "Создаю релиз ${TAG}..."
    payload=$(printf '{"tag_name":"%s","name":"%s","body":"Автоматический релиз","draft":false,"prerelease":false}' "$TAG" "$TAG")
    response=$(curl -s -H "$AUTH_HEADER" -H "Content-Type: application/json" -d "$payload" "${API}/releases")
    release_id=$(echo "$response" | grep -m1 '"id"' | grep -o '[0-9]*')
    [ -n "$release_id" ] || fail "Не удалось создать релиз: $response"
fi

existing_asset_id=$(curl -s -H "$AUTH_HEADER" "${API}/releases/${release_id}/assets" | grep -B3 '"name": *"install.sh"' | grep -m1 '"id"' | grep -o '[0-9]*' || true)
if [ -n "${existing_asset_id:-}" ]; then
    curl -s -X DELETE -H "$AUTH_HEADER" "${API}/releases/assets/${existing_asset_id}" >/dev/null
fi

curl -s -H "$AUTH_HEADER" -H "Content-Type: application/x-sh" \
    --data-binary @install.sh \
    "https://uploads.github.com/repos/${REPO}/releases/${release_id}/assets?name=install.sh" >/dev/null

info "Релиз ${TAG} готов: https://github.com/${REPO}/releases/tag/${TAG}"
info "Установка по-прежнему: curl -fsSL https://raw.githubusercontent.com/${REPO}/stable/install.sh | bash"
