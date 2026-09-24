# Shared, pinned source identity. The VM remains on the original 2019.4 patch.
case "${BATADV_PROFILE:-${KERNEL_RELEASE:-$(uname -r)}}" in
  legacy|5.4.*)
    UPSTREAM_TAG=v2019.4
    UPSTREAM_COMMIT=933568baeba83d6bcaa451656ec1550346f35996
    MODULE_VERSION=2019.4-macewifi1
    NATIVE_BUILD_VERSION=2019.4-macev1
    PATCH_FILE="$SCRIPT_DIR/patches/0001-batman-adv-add-emulated-wifi-hardif.patch"
    ;;
  modern|6.8.*)
    UPSTREAM_TAG=v2024.0
    UPSTREAM_COMMIT=7ee009fb21955bc7977d96b00eb8362a558d0d3a
    MODULE_VERSION=2024.0-macewifi1
    NATIVE_BUILD_VERSION=2024.0-macev1
    PATCH_FILE="$SCRIPT_DIR/patches/0002-batman-adv-2024.0-emulated-wifi.patch"
    ;;
  *)
    echo "[ERROR] Kernel has no validated build profile. Use 5.4/6.8 or explicitly select BATADV_PROFILE after reviewing compatibility." >&2
    exit 1
    ;;
esac
