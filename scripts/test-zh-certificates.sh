#!/usr/bin/env bash
# All certificate and ACME operations below are isolated mocks.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
fixture=$(mktemp -d "$repo/.certificate-test.XXXXXXXX")
trap 'rm -rf -- "$fixture"' EXIT
export fixture HOME=$fixture/home
mkdir -p "$HOME/.acme.sh" "$fixture/cert/ip" "$fixture/cert/example.com" "$fixture/cert/other.example.com"
export xui_cert_root=$fixture/cert
printf original-cert > "$fixture/cert/ip/fullchain.pem"
printf original-key > "$fixture/cert/ip/privkey.pem"
source "$repo/scripts/zh-helpers.sh"
xui_migrated() { return 0; }
[[ $(xui_certificate_root) == /root/cert ]]
xui_migrated() { return 1; }
[[ $(xui_certificate_root) == /etc/x-ui-managed-certs ]]
sed -n '/^is_domain()/,/^}/p' "$repo/scripts/zh-ops.sh" > "$fixture/functions.sh"
sed -n '/^certificate_list_names()/,/^ssl_cert_issue_main()/p' "$repo/scripts/zh-ops.sh" | sed '$d' >> "$fixture/functions.sh"
source "$fixture/functions.sh"
LOGI() { printf '信息：%s\n' "$*"; }
LOGE() { printf '错误：%s\n' "$*" >&2; }
if [[ -n ${XUI_TEST_PYTHON:-} ]]; then python3() { "$XUI_TEST_PYTHON" "$@"; }; fi
openssl() { printf 'X509v3 Subject Alternative Name:\n    IP Address:198.51.100.10, IP Address:2001:DB8::10\n'; }
cat > "$HOME/.acme.sh/acme.sh" <<'SH'
#!/usr/bin/env bash
printf '%s|' "$@" >> "$fixture/acme-calls"
printf '\n' >> "$fixture/acme-calls"
case $1 in
    --list)
        printf 'Main_Domain KeyLength SAN\nexample.com ec-256 no\n198.51.100.10 ec-256 no\n203.0.113.77 ec-256 no\n'
        [[ ! -f $fixture/ambiguous ]] || printf '2001:db8::10 ec-256 no\n' ;;
    --renew) [[ ! -f $fixture/fail-renew ]] ;;
    --installcert) [[ ! -f $fixture/fail-register ]] ;;
    --revoke) [[ ! -f $fixture/fail-revoke ]] ;;
    --remove) : ;;
    --set-default-ca) : ;;
    --register-account) [[ ! -f $fixture/fail-account ]] ;;
    *) exit 1 ;;
esac
SH
chmod +x "$HOME/.acme.sh/acme.sh"
names=$(certificate_list_names)
certificate_selection_valid example.com "$names"
certificate_selection_valid ip "$names"
for invalid in com '../example.com' 'example.com/..' '--help' 'example.com'$'\n''other.example.com'; do
    if certificate_selection_valid "$invalid" "$names"; then printf 'unsafe selection accepted\n' >&2; exit 1; fi
done
if certificate_selection_valid example.com exampleXcom; then exit 1; fi
printf 'PASS original/new certificate roots and literal safe selection\n'

[[ $(certificate_acme_id ip) == 198.51.100.10 ]]
certificate_renew ip > "$fixture/output"
grep -Fxq -- '--renew|-d|198.51.100.10|--force|' "$fixture/acme-calls"
! grep -Fxq -- '--renew|-d|ip|--force|' "$fixture/acme-calls"
touch "$fixture/ambiguous"
if certificate_acme_id ip > "$fixture/output" 2>&1; then exit 1; fi
rm "$fixture/ambiguous"
printf 'PASS IP renewal selects actual certificate SAN tracking ID\n'

touch "$fixture/fail-renew" "$fixture/fail-register" "$fixture/fail-revoke"
if certificate_renew example.com > "$fixture/output" 2>&1; then exit 1; fi
! grep -Fq '已强制续期' "$fixture/output"
if certificate_register_hook example.com "$fixture/cert/example.com/fullchain.pem" "$fixture/cert/example.com/privkey.pem" > "$fixture/output" 2>&1; then exit 1; fi
! grep -Fq '已为' "$fixture/output"
if certificate_revoke ip > "$fixture/output" 2>&1; then exit 1; fi
[[ $(<"$fixture/cert/ip/privkey.pem") == original-key ]]
[[ -d $fixture/cert/other.example.com ]]
! grep -Fq -- '--remove|' "$fixture/acme-calls"
printf 'PASS failed renewal/registration/revocation never report success or delete originals\n'

rm "$fixture/fail-renew" "$fixture/fail-register" "$fixture/fail-revoke"
certificate_register_hook example.com "$fixture/cert/example.com/fullchain.pem" "$fixture/cert/example.com/privkey.pem" > "$fixture/output"
grep -Fq '已为 example.com 注册' "$fixture/output"
certificate_revoke example.com > "$fixture/output"
[[ ! -e $fixture/cert/example.com && -d $fixture/cert/ip && -d $fixture/cert/other.example.com ]]
printf 'PASS successful registration and scoped revocation\n'

# Execute the actual provisioning guard as a function; failed account registration
# must return before any issuance and must not claim issuance success.
{
    printf 'registration_before_issue() {\n'
    sed -n '/^    # Issue certificate with shortlived profile/,/^    ~\/.acme.sh\/acme.sh --issue/p' "$repo/scripts/zh-provision.sh" | sed '$d'
    printf 'printf issue-would-run >> "$fixture/issuance"\n}\n'
} > "$fixture/registration.sh"
source "$fixture/registration.sh"
XUI_ACME_EMAIL=admin@example.com
ipv4=198.51.100.10
green= plain=
touch "$fixture/fail-account"
if registration_before_issue > "$fixture/output" 2>&1; then exit 1; fi
[[ ! -e $fixture/issuance ]]
grep -Fq 'ACME 账号注册失败' "$fixture/output"
printf 'PASS failed ACME account registration stops before issuance\n'
