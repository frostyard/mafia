#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:-"$root/dist"}"
version="$(
  python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])'
)"
release_name="mafia-$version"
release_dir="$output_dir/$release_name"
archive="$output_dir/$release_name.tar.gz"

if [[ -e "$release_dir" || -e "$archive" ]]; then
  echo "Release output already exists: $release_dir or $archive" >&2
  exit 1
fi

mkdir -p "$output_dir"
staging="$(mktemp -d "$output_dir/.${release_name}.XXXXXX")"
cleanup() {
  if [[ -n "${staging:-}" && -d "$staging" ]]; then
    rm -rf -- "$staging"
  fi
}
trap cleanup EXIT

cd "$root"
npm --prefix apps/web run build

mkdir -p \
  "$staging/$release_name/api" \
  "$staging/$release_name/apps/api" \
  "$staging/$release_name/bin" \
  "$staging/$release_name/contrib" \
  "$staging/$release_name/docs" \
  "$staging/$release_name/web"

uv build --wheel --out-dir "$staging/$release_name/api" --no-create-gitignore
uv export \
  --frozen \
  --no-dev \
  --no-emit-project \
  --no-header \
  --output-file "$staging/$release_name/api/requirements.txt"

cp -a apps/web/.next/standalone/. "$staging/$release_name/web/"
mkdir -p "$staging/$release_name/web/.next"
cp -a apps/web/.next/static "$staging/$release_name/web/.next/static"
if [[ -d apps/web/public ]]; then
  cp -a apps/web/public "$staging/$release_name/web/public"
fi

cp -a apps/api/migrations "$staging/$release_name/apps/api/migrations"
find "$staging/$release_name/apps/api/migrations" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$staging/$release_name/apps/api/migrations" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
cp -a packaging/bin/. "$staging/$release_name/bin/"
cp -a contrib/. "$staging/$release_name/contrib/"
cp contrib/start.sh "$staging/$release_name/start.sh"
cp packaging/launch.cjs "$staging/$release_name/web/launch.cjs"
cp alembic.ini .env.example LICENSE "$staging/$release_name/"
cp docs/deployment.md "$staging/$release_name/README.md"
cp docs/authentication.md "$staging/$release_name/docs/authentication.md"
cp docs/incus.md "$staging/$release_name/docs/incus.md"
cp docs/frostyard-incus.md "$staging/$release_name/docs/frostyard-incus.md"
printf '%s\n' "$version" >"$staging/$release_name/VERSION"
chmod +x "$staging/$release_name"/bin/* "$staging/$release_name/start.sh"

tar -C "$staging" -czf "$staging/$release_name.tar.gz" "$release_name"
mv "$staging/$release_name" "$release_dir"
mv "$staging/$release_name.tar.gz" "$archive"

printf 'Release directory: %s\nRelease archive: %s\n' "$release_dir" "$archive"
