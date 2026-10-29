#!/bin/sh
# Inspectable Rhizome bootstrap. Installs only an explicitly pinned Git commit.
set -eu
umask 077
LC_ALL=C
export LC_ALL

UV_VERSION=0.11.7
PYTHON_VERSION=3.13.13
PROJECT_URL=https://github.com/Doctacon/rhizome
RAW_URL=https://raw.githubusercontent.com/Doctacon/rhizome
OWNER_MARKER=rhizome-install-root-v1
ACTION=
REVISION=
TMP_WORK=
ROOT_STAGE=
REVISION_STAGE=
PUBLICATION_STAGE=
INSTALL_LOCK=
LOCK_NONCE=
LOCK_QUARANTINE=
ROOT_PARENT=
ROOT_NAME=
UNINSTALL_STAGE=

usage() {
    cat <<'EOF'
Usage:
  install.sh --revision FULL_40_HEX_COMMIT
  install.sh --rollback
  install.sh --uninstall

Installation and updates are always explicit. A revision must be a full lowercase
40-character Git commit; moving branches and "latest" are not accepted.
EOF
}

fail() {
    printf 'rhizome bootstrap: %s\n' "$*" >&2
    exit 1
}

usage_error() {
    printf 'rhizome bootstrap: %s\n' "$*" >&2
    usage >&2
    exit 2
}

is_revision() {
    [ "${#1}" -eq 40 ] || return 1
    case "$1" in
        *[!0-9a-f]*) return 1 ;;
    esac
    return 0
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ -n "$INSTALL_LOCK" ] && [ -n "$LOCK_NONCE" ] &&
        lock_record_matches "$INSTALL_LOCK" "$$" "$LOCK_NONCE" &&
        { [ -e "$INSTALL_LOCK/activity" ] || [ -L "$INSTALL_LOCK/activity" ]; }; then
        # A wrapper or an orphaned descendant may still own these paths.
        # Preserve all marked state and the lock while activity is unverifiable.
        exit "$status"
    fi
    if [ -n "$TMP_WORK" ] && [ -d "$TMP_WORK" ] && [ ! -L "$TMP_WORK" ]; then
        rm -rf "$TMP_WORK"
    fi
    if [ -n "$REVISION_STAGE" ] && [ -d "$REVISION_STAGE" ] && [ ! -L "$REVISION_STAGE" ]; then
        marker=$REVISION_STAGE/.rhizome-installing
        if [ ! -L "$marker" ] && [ -f "$marker" ] &&
            [ "$(sed -n '1p' "$marker" 2>/dev/null || :)" = "$REVISION" ]; then
            guarded_command rm -rf "$REVISION_STAGE" || :
        fi
    fi
    if [ -n "$PUBLICATION_STAGE" ] && [ -d "$PUBLICATION_STAGE" ] &&
        [ ! -L "$PUBLICATION_STAGE" ]; then
        marker=$PUBLICATION_STAGE/.rhizome-revision
        if [ ! -L "$marker" ] && [ -f "$marker" ] &&
            [ "$(cat "$marker" 2>/dev/null || :)" = "$REVISION" ]; then
            guarded_command rm -rf "$PUBLICATION_STAGE" || :
        fi
    fi
    if [ -n "$INSTALL_LOCK" ] && [ -n "$LOCK_NONCE" ] &&
        lock_record_matches "$INSTALL_LOCK" "$$" "$LOCK_NONCE" &&
        [ ! -e "$INSTALL_LOCK/activity" ] && [ ! -L "$INSTALL_LOCK/activity" ]; then
        rm -rf "$INSTALL_LOCK"
    fi
    if [ -n "$LOCK_QUARANTINE" ] && [ -n "$LOCK_NONCE" ] &&
        quarantine_matches_reaper "$LOCK_QUARANTINE" "$$" "$LOCK_NONCE"; then
        rm -rf "$LOCK_QUARANTINE"
    fi
    if [ -n "$ROOT_STAGE" ] && [ -d "$ROOT_STAGE" ] && [ ! -L "$ROOT_STAGE" ]; then
        marker=$ROOT_STAGE/.rhizome-install-root
        if { [ ! -L "$marker" ] && [ -f "$marker" ] &&
            [ "$(cat "$marker" 2>/dev/null || :)" = "$OWNER_MARKER" ]; } ||
            [ -z "$(ls -A "$ROOT_STAGE" 2>/dev/null)" ]; then
            guarded_command rm -rf "$ROOT_STAGE" || :
        fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

while [ "$#" -gt 0 ]; do
    case "$1" in
        --revision)
            [ -z "$ACTION" ] || usage_error "choose exactly one action"
            [ "$#" -ge 2 ] || usage_error "--revision requires a commit"
            ACTION=install
            REVISION=$2
            shift 2
            ;;
        --rollback)
            [ -z "$ACTION" ] || usage_error "choose exactly one action"
            ACTION=rollback
            shift
            ;;
        --uninstall)
            [ -z "$ACTION" ] || usage_error "choose exactly one action"
            ACTION=uninstall
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) usage_error "unknown argument: $1" ;;
    esac
done

[ -n "$ACTION" ] || usage_error "--revision requires a 40-character lowercase hexadecimal commit"
if [ "$ACTION" = install ] && ! is_revision "$REVISION"; then
    usage_error "--revision requires a 40-character lowercase hexadecimal commit"
fi

if [ -n "${RHIZOME_INSTALL_ROOT:-}" ]; then
    ROOT=$RHIZOME_INSTALL_ROOT
else
    DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
    ROOT=$DATA_HOME/rhizome
fi

state_path() {
    if [ -n "${RHIZOME_STATE_HOME:-}" ]; then
        printf '%s\n' "$RHIZOME_STATE_HOME"
    elif [ -n "${XDG_STATE_HOME:-}" ]; then
        printf '%s/rhizome\n' "$XDG_STATE_HOME"
    elif [ "$(uname -s)" = Darwin ]; then
        printf '%s/Library/Application Support/Rhizome\n' "$HOME"
    else
        printf '%s/.local/state/rhizome\n' "$HOME"
    fi
}
STATE_PATH=$(state_path)

validate_absolute_path() {
    label=$1
    value=$2
    case "$value" in
        /*) ;;
        *) fail "$label must be an absolute path: $value" ;;
    esac
    case "$value" in
        /|*/../*|*/..|*/./*|*/.|*//*) fail "refusing unsafe or non-normalized $label: $value" ;;
    esac
}
validate_absolute_path "installation root" "$ROOT"
[ "$ROOT" != "$HOME" ] || fail "refusing unsafe or non-normalized installation root: $ROOT"
validate_absolute_path "Rhizome state location" "$STATE_PATH"

# Resolve an existing directory or the nearest existing ancestor of a future path.
# The lexical checks above make appending the not-yet-created suffix unambiguous.
physical_path() {
    candidate=$1
    suffix=
    while [ ! -e "$candidate" ] && [ ! -L "$candidate" ]; do
        base=${candidate##*/}
        [ -n "$base" ] || return 1
        suffix=/$base$suffix
        candidate=${candidate%/*}
        [ -n "$candidate" ] || candidate=/
    done
    [ -d "$candidate" ] || return 1
    physical=$(CDPATH= cd "$candidate" && pwd -P) || return 1
    printf '%s%s\n' "$physical" "$suffix"
}

assert_state_outside_root() {
    physical_root=$(physical_path "$ROOT") ||
        fail "cannot prove the physical installation root: $ROOT"
    physical_state=$(physical_path "$STATE_PATH") ||
        fail "cannot prove the physical Rhizome state location: $STATE_PATH"
    case "$physical_state" in
        "$physical_root"|"$physical_root"/*)
            fail "installation root must not contain Rhizome drafts or recovery data: $STATE_PATH"
            ;;
    esac
}
assert_state_outside_root

lock_record_matches() {
    lock_directory=$1
    expected_pid=$2
    expected_nonce=$3
    lock_owner=$lock_directory/owner
    [ ! -L "$lock_directory" ] && [ -d "$lock_directory" ] &&
        [ ! -L "$lock_owner" ] && [ -f "$lock_owner" ] || return 1
    [ "$(sed -n '1p' "$lock_owner" 2>/dev/null || :)" = "$expected_pid" ] &&
        [ "$(sed -n '2p' "$lock_owner" 2>/dev/null || :)" = "$expected_nonce" ] &&
        [ -z "$(sed -n '3p' "$lock_owner" 2>/dev/null || :)" ]
}

quarantine_matches_reaper() {
    quarantine=$1
    expected_pid=$2
    expected_nonce=$3
    reaper=$quarantine/reaper
    lock_record_matches "$reaper" "$expected_pid" "$expected_nonce"
}

assert_no_lock_activity() {
    lock_directory=$1
    if [ -e "$lock_directory/activity" ] || [ -L "$lock_directory/activity" ]; then
        fail "transaction subprocess activity is active or unverifiable; manual inspection is required: $lock_directory"
    fi
}

# The owner records activity before the foreground command starts. If its wrapper
# or an intermediate launcher is signaled, descendants may outlive it. Retain the
# fence for manual inspection rather than guessing when orphaned writers finish.
guarded_command() {
    [ -n "$INSTALL_LOCK" ] && [ -n "$LOCK_NONCE" ] ||
        fail "cannot guard a command without transaction-lock ownership"
    activity=$INSTALL_LOCK/activity
    [ ! -e "$activity" ] && [ ! -L "$activity" ] ||
        fail "transaction subprocess activity is already present; manual inspection is required"
    printf '%s\n' "$LOCK_NONCE" > "$activity" ||
        fail "cannot record transaction subprocess activity"
    (
        activity_nonce=$LOCK_NONCE
        clear_activity() {
            if [ ! -L "$activity" ] && [ -f "$activity" ] &&
                [ "$(cat "$activity" 2>/dev/null || :)" = "$activity_nonce" ]; then
                rm -f "$activity"
            fi
        }
        trap clear_activity EXIT
        trap 'trap - EXIT; exit 129' HUP
        trap 'trap - EXIT; exit 130' INT
        trap 'trap - EXIT; exit 143' TERM
        set +e
        "$@"
        command_status=$?
        # POSIX shells report signal deaths with a status greater than 128.
        # An intentional high exit code is indistinguishable, so also fail closed.
        # Ordinary failures retain the existing cleanup/retry behavior.
        if [ "$command_status" -le 128 ]; then
            clear_activity
        else
            printf 'rhizome bootstrap: command ended with status %s; subprocess activity retained for manual inspection: %s\n' \
                "$command_status" "$INSTALL_LOCK" >&2
        fi
        trap - EXIT HUP INT TERM
        exit "$command_status"
    )
}

read_lock_record() {
    lock_directory=$1
    lock_owner=$lock_directory/owner
    [ ! -L "$lock_directory" ] && [ -d "$lock_directory" ] &&
        [ ! -L "$lock_owner" ] && [ -f "$lock_owner" ] || return 1
    LOCK_RECORD_PID=$(sed -n '1p' "$lock_owner" 2>/dev/null || :)
    LOCK_RECORD_NONCE=$(sed -n '2p' "$lock_owner" 2>/dev/null || :)
    case "$LOCK_RECORD_PID" in ''|*[!0-9]*) return 1 ;; esac
    [ -n "$LOCK_RECORD_NONCE" ] || return 1
    [ -z "$(sed -n '3p' "$lock_owner" 2>/dev/null || :)" ]
}

remove_proven_quarantine() {
    quarantine=$1
    [ ! -L "$quarantine" ] && [ -d "$quarantine" ] ||
        fail "unsafe transaction-lock quarantine; manual inspection is required: $quarantine"
    read_lock_record "$quarantine" ||
        fail "incomplete transaction-lock quarantine; manual inspection is required: $quarantine"
    abandoned_pid=$LOCK_RECORD_PID
    abandoned_nonce=$LOCK_RECORD_NONCE
    reaper=$quarantine/reaper
    read_lock_record "$reaper" ||
        fail "interrupted transaction-lock reaper cannot be verified; manual inspection is required: $quarantine"
    reaper_pid=$LOCK_RECORD_PID
    assert_no_lock_activity "$quarantine"
    if kill -0 "$abandoned_pid" 2>/dev/null || kill -0 "$reaper_pid" 2>/dev/null; then
        fail "transaction-lock recovery is still active; manual inspection is required if it does not finish"
    fi
    # The outer lock was atomically moved here by the uniquely recorded, now-dead
    # reaper. Removing this private quarantine cannot remove a subsequently claimed lock.
    rm -rf "$quarantine"
}

check_lock_quarantines() {
    ignored_nonce=${1:-}
    for quarantine in "$INSTALL_LOCK".quarantine.*; do
        [ -e "$quarantine" ] || [ -L "$quarantine" ] || continue
        if [ -n "$ignored_nonce" ] &&
            [ "$quarantine" = "$INSTALL_LOCK.quarantine.$ignored_nonce" ]; then
            continue
        fi
        remove_proven_quarantine "$quarantine"
    done
}

acquire_transaction_lock() {
    parent=${ROOT%/*}
    [ -n "$parent" ] || parent=/
    mkdir -p "$parent" || fail "cannot prepare the installation parent directory"
    physical_parent=$(physical_path "$parent") ||
        fail "cannot resolve the physical installation parent directory"
    root_name=${ROOT##*/}
    [ -n "$root_name" ] || fail "refusing unsafe installation root"
    ROOT=$physical_parent/$root_name
    [ "$physical_parent" != / ] || ROOT=/$root_name
    ROOT_PARENT=$physical_parent
    ROOT_NAME=$root_name
    UNINSTALL_STAGE=$ROOT_PARENT/.rhizome-$ROOT_NAME.uninstalling

    nonce_source=$(mktemp -d "$physical_parent/.rhizome-lock-nonce.XXXXXX") ||
        fail "cannot create a private transaction-lock nonce"
    LOCK_NONCE=${nonce_source##*/}
    rmdir "$nonce_source" || fail "cannot finalize the transaction-lock nonce"
    INSTALL_LOCK=$physical_parent/.rhizome-$root_name.transaction.lock

    check_lock_quarantines
    if mkdir "$INSTALL_LOCK" 2>/dev/null; then
        printf '%s\n%s\n' "$$" "$LOCK_NONCE" > "$INSTALL_LOCK/owner" ||
            fail "cannot record transaction-lock ownership; manual inspection is required"
        # Close the scan/create race with a stale-lock reaper. We do not begin
        # the transaction while another live reaper quarantine is present.
        check_lock_quarantines
        return
    fi

    read_lock_record "$INSTALL_LOCK" ||
        fail "unsafe or incomplete Rhizome transaction lock; manual inspection is required: $INSTALL_LOCK"
    stale_pid=$LOCK_RECORD_PID
    stale_nonce=$LOCK_RECORD_NONCE
    if kill -0 "$stale_pid" 2>/dev/null; then
        fail "another Rhizome installation transaction is active"
    fi
    assert_no_lock_activity "$INSTALL_LOCK"

    reaper=$INSTALL_LOCK/reaper
    if ! mkdir "$reaper" 2>/dev/null; then
        fail "an interrupted transaction-lock reaper requires manual inspection: $INSTALL_LOCK"
    fi
    printf '%s\n%s\n' "$$" "$LOCK_NONCE" > "$reaper/owner" ||
        fail "cannot record transaction-lock reaper ownership; manual inspection is required"

    # Ownership can change only if another claimant moved and replaced the
    # whole outer lock. Re-read it and recheck liveness after the exclusive claim.
    read_lock_record "$INSTALL_LOCK" ||
        fail "transaction-lock ownership changed during recovery; manual inspection is required"
    [ "$LOCK_RECORD_PID" = "$stale_pid" ] && [ "$LOCK_RECORD_NONCE" = "$stale_nonce" ] ||
        fail "transaction-lock ownership changed during recovery; manual inspection is required"
    if kill -0 "$stale_pid" 2>/dev/null; then
        fail "transaction-lock owner became active during recovery; manual inspection is required"
    fi
    assert_no_lock_activity "$INSTALL_LOCK"

    LOCK_QUARANTINE=$INSTALL_LOCK.quarantine.$LOCK_NONCE
    [ ! -e "$LOCK_QUARANTINE" ] && [ ! -L "$LOCK_QUARANTINE" ] ||
        fail "transaction-lock quarantine collision; manual inspection is required"
    mv "$INSTALL_LOCK" "$LOCK_QUARANTINE" ||
        fail "could not quarantine the stale transaction lock"
    if ! mkdir "$INSTALL_LOCK" 2>/dev/null; then
        fail "another Rhizome transaction claimed the lock during stale recovery"
    fi
    printf '%s\n%s\n' "$$" "$LOCK_NONCE" > "$INSTALL_LOCK/owner" ||
        fail "cannot record transaction-lock ownership; manual inspection is required"
    check_lock_quarantines "$LOCK_NONCE"
    rm -rf "$LOCK_QUARANTINE"
    LOCK_QUARANTINE=
}

validate_top_level() {
    for entry in "$ROOT"/* "$ROOT"/.[!.]* "$ROOT"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        name=${entry##*/}
        case "$name" in
            .rhizome-install-root|revisions|store|bin|transactions|current|previous) ;;
            *) fail "unexpected foreign top-level content in Rhizome installation: $entry" ;;
        esac
    done
}

validate_managed_subtrees() {
    for entry in "$ROOT/revisions"/* "$ROOT/revisions"/.[!.]* "$ROOT/revisions"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        name=${entry##*/}
        revision=$name
        case "$name" in .staging-*) revision=${name#.staging-} ;; esac
        is_revision "$revision" ||
            fail "unexpected foreign content in Rhizome revisions: $entry"
        [ ! -L "$entry" ] && [ -d "$entry" ] ||
            fail "unsafe Rhizome revision content: $entry"
        marker=$entry/.rhizome-revision
        [ ! -L "$marker" ] && [ -f "$marker" ] &&
            [ "$(cat "$marker" 2>/dev/null || :)" = "$revision" ] ||
            fail "unmarked Rhizome revision content: $entry"
    done

    for entry in "$ROOT/store"/* "$ROOT/store"/.[!.]* "$ROOT/store"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        name=${entry##*/}
        revision=$name
        case "$name" in .staging-*) revision=${name#.staging-} ;; esac
        is_revision "$revision" ||
            fail "unexpected foreign content in Rhizome payload store: $entry"
        [ ! -L "$entry" ] && [ -d "$entry" ] ||
            fail "unsafe Rhizome payload content: $entry"
        complete=$entry/.rhizome-revision
        installing=$entry/.rhizome-installing
        if [ ! -L "$complete" ] && [ -f "$complete" ] &&
            [ "$(cat "$complete" 2>/dev/null || :)" = "$revision" ]; then
            :
        elif [ ! -L "$installing" ] && [ -f "$installing" ] &&
            [ "$(sed -n '1p' "$installing" 2>/dev/null || :)" = "$revision" ]; then
            :
        else
            fail "unmarked Rhizome payload content: $entry"
        fi
    done

    for entry in "$ROOT/bin"/* "$ROOT/bin"/.[!.]* "$ROOT/bin"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        name=${entry##*/}
        case "$name" in .rhizome-owned|rhizome|uv) ;;
            *) fail "unexpected foreign content in Rhizome launcher directory: $entry" ;;
        esac
        [ ! -L "$entry" ] && [ -f "$entry" ] ||
            fail "unsafe Rhizome launcher content: $entry"
    done

    for entry in "$ROOT/transactions"/* "$ROOT/transactions"/.[!.]* \
        "$ROOT/transactions"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        name=${entry##*/}
        case "$name" in
            rollback|.current.new.*|.previous.new.*|.rhizome-launcher.new.*|.uv-launcher.new.*) ;;
            *) fail "unexpected foreign content in Rhizome transactions: $entry" ;;
        esac
        [ ! -L "$entry" ] && [ -f "$entry" ] ||
            fail "unsafe Rhizome transaction content: $entry"
    done
}

require_owned_root() {
    [ ! -L "$ROOT" ] || fail "refusing symlink installation root: $ROOT"
    [ -d "$ROOT" ] || fail "Rhizome is not installed at $ROOT"
    ROOT=$(physical_path "$ROOT") || fail "cannot resolve physical installation root: $ROOT"
    marker=$ROOT/.rhizome-install-root
    [ ! -L "$marker" ] && [ -f "$marker" ] ||
        fail "$ROOT is not marked as Rhizome-owned; refusing to modify it"
    [ "$(cat "$marker" 2>/dev/null || :)" = "$OWNER_MARKER" ] ||
        fail "$ROOT is not marked as Rhizome-owned; refusing to modify it"
    [ ! -L "$ROOT/revisions" ] && [ -d "$ROOT/revisions" ] ||
        fail "unsafe Rhizome revisions directory at $ROOT/revisions"
    [ ! -L "$ROOT/store" ] && [ -d "$ROOT/store" ] ||
        fail "unsafe Rhizome payload directory at $ROOT/store"
    [ ! -L "$ROOT/bin" ] && [ -d "$ROOT/bin" ] ||
        fail "unsafe Rhizome launcher directory at $ROOT/bin"
    if [ -e "$ROOT/transactions" ] || [ -L "$ROOT/transactions" ]; then
        [ ! -L "$ROOT/transactions" ] && [ -d "$ROOT/transactions" ] ||
            fail "unsafe Rhizome transactions directory at $ROOT/transactions"
    fi
    validate_top_level
    validate_managed_subtrees
    for pointer in current previous; do
        path=$ROOT/$pointer
        if [ -e "$path" ] || [ -L "$path" ]; then
            [ ! -L "$path" ] && [ -f "$path" ] ||
                fail "unsafe $pointer pointer at $path"
            revision_from_pointer "$pointer" >/dev/null
        fi
    done
}

revision_target() {
    target=$1
    case "$target" in
        revisions/*) revision=${target#revisions/} ;;
        *) fail "unsafe revision pointer target: $target" ;;
    esac
    is_revision "$revision" || fail "unsafe revision pointer target: $target"
    [ "$target" = "revisions/$revision" ] || fail "unsafe revision pointer target: $target"
    record=$ROOT/$target
    [ ! -L "$record" ] && [ -d "$record" ] || fail "missing selected revision"
    record_marker=$record/.rhizome-revision
    [ ! -L "$record_marker" ] && [ -f "$record_marker" ] &&
        [ "$(cat "$record_marker" 2>/dev/null || :)" = "$revision" ] ||
        fail "unmarked selected revision"
    directory=$ROOT/store/$revision
    [ ! -L "$directory" ] && [ -d "$directory" ] || fail "missing selected revision payload"
    marker=$directory/.rhizome-revision
    [ ! -L "$marker" ] && [ -f "$marker" ] &&
        [ "$(cat "$marker" 2>/dev/null || :)" = "$revision" ] ||
        fail "unmarked selected revision payload"
    [ -x "$directory/bin/rhizome" ] || fail "unusable selected revision"
    printf '%s\n' "$target"
}

revision_from_pointer() {
    pointer=$1
    path=$ROOT/$pointer
    [ ! -L "$path" ] && [ -f "$path" ] || fail "unsafe or missing $pointer pointer at $path"
    target=$(cat "$path") || fail "cannot read $pointer pointer"
    revision_target "$target"
}

ensure_transactions() {
    if [ ! -e "$ROOT/transactions" ]; then
        mkdir "$ROOT/transactions"
    fi
    [ ! -L "$ROOT/transactions" ] && [ -d "$ROOT/transactions" ] ||
        fail "unsafe Rhizome transactions directory at $ROOT/transactions"
}

atomic_pointer() {
    name=$1
    target=$2
    ensure_transactions
    temporary=$ROOT/transactions/.${name}.new.$$
    rm -f "$temporary"
    printf '%s\n' "$target" > "$temporary"
    mv -f "$temporary" "$ROOT/$name"
}

print_launch() {
    printf '\nLaunch Rhizome directly (no PATH or profile edit was made):\n  %s [REPOSITORY]\n' \
        "$ROOT/bin/rhizome"
    printf 'This action does not restart any running Rhizome process.\n'
}

finish_rollback() {
    journal=$ROOT/transactions/rollback
    [ ! -L "$journal" ] && [ -f "$journal" ] || fail "unsafe rollback journal"
    [ "$(sed -n '1p' "$journal")" = "rhizome-rollback-v1" ] || fail "unsafe rollback journal"
    to_current=$(sed -n '2p' "$journal")
    to_previous=$(sed -n '3p' "$journal")
    [ -n "$to_current" ] && [ -n "$to_previous" ] || fail "incomplete rollback journal"
    [ -z "$(sed -n '4p' "$journal")" ] || fail "unsafe rollback journal"
    revision_target "$to_current" >/dev/null
    revision_target "$to_previous" >/dev/null
    atomic_pointer current "$to_current"
    atomic_pointer previous "$to_previous"
    rm "$journal"
}

scavenge_root_staging() {
    for stage in "$ROOT_PARENT"/.rhizome-root.*.staging; do
        [ -e "$stage" ] || [ -L "$stage" ] || continue
        name=${stage##*/}
        stage_pid=${name#.rhizome-root.}
        stage_pid=${stage_pid%.staging}
        case "$stage_pid" in ''|*[!0-9]*)
            fail "unsafe first-root staging remnant; manual inspection is required: $stage" ;;
        esac
        [ ! -L "$stage" ] && [ -d "$stage" ] ||
            fail "unsafe first-root staging remnant; manual inspection is required: $stage"
        stage_marker=$stage/.rhizome-install-root
        [ ! -L "$stage_marker" ] && [ -f "$stage_marker" ] &&
            [ "$(cat "$stage_marker" 2>/dev/null || :)" = "$OWNER_MARKER" ] ||
            fail "unverifiable first-root staging remnant; manual inspection is required: $stage"
        for entry in "$stage"/* "$stage"/.[!.]* "$stage"/..?*; do
            [ -e "$entry" ] || [ -L "$entry" ] || continue
            entry_name=${entry##*/}
            case "$entry_name" in
                .rhizome-install-root) ;;
                revisions|store|bin|transactions)
                    [ ! -L "$entry" ] && [ -d "$entry" ] &&
                        [ -z "$(ls -A "$entry" 2>/dev/null)" ] ||
                        fail "nonempty first-root staging remnant; manual inspection is required: $stage"
                    ;;
                *) fail "foreign first-root staging content; manual inspection is required: $entry" ;;
            esac
        done
        guarded_command rm -rf "$stage"
    done
}

validate_uninstall_stage() {
    [ ! -L "$UNINSTALL_STAGE" ] && [ -d "$UNINSTALL_STAGE" ] ||
        fail "unsafe uninstall recovery transaction; manual inspection is required: $UNINSTALL_STAGE"
    uninstall_marker=$UNINSTALL_STAGE/owner
    [ ! -L "$uninstall_marker" ] && [ -f "$uninstall_marker" ] ||
        fail "incomplete uninstall recovery transaction; manual inspection is required: $UNINSTALL_STAGE"
    [ "$(sed -n '1p' "$uninstall_marker" 2>/dev/null || :)" = "rhizome-uninstall-v1" ] &&
        [ "$(sed -n '2p' "$uninstall_marker" 2>/dev/null || :)" = "$ROOT" ] &&
        [ -z "$(sed -n '3p' "$uninstall_marker" 2>/dev/null || :)" ] ||
        fail "unsafe uninstall recovery transaction; manual inspection is required: $UNINSTALL_STAGE"
    for entry in "$UNINSTALL_STAGE"/* "$UNINSTALL_STAGE"/.[!.]* \
        "$UNINSTALL_STAGE"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        case "${entry##*/}" in
            owner) [ ! -L "$entry" ] && [ -f "$entry" ] ||
                fail "unsafe uninstall recovery marker: $entry" ;;
            root) [ ! -L "$entry" ] && [ -d "$entry" ] ||
                fail "unsafe uninstall recovery payload: $entry" ;;
            *) fail "foreign uninstall recovery content; manual inspection is required: $entry" ;;
        esac
    done
}

recover_uninstall() {
    [ -e "$UNINSTALL_STAGE" ] || [ -L "$UNINSTALL_STAGE" ] || return 0
    validate_uninstall_stage
    staged_root=$UNINSTALL_STAGE/root
    if [ -e "$ROOT" ] || [ -L "$ROOT" ]; then
        [ ! -e "$staged_root" ] && [ ! -L "$staged_root" ] ||
            fail "ambiguous uninstall recovery transaction; manual inspection is required"
        rm "$UNINSTALL_STAGE/owner"
        rmdir "$UNINSTALL_STAGE"
        return 0
    fi
    if [ -e "$staged_root" ] || [ -L "$staged_root" ]; then
        guarded_command rm -rf "$staged_root" ||
            fail "could not complete the owned uninstall recovery transaction"
    fi
    rm "$UNINSTALL_STAGE/owner"
    rmdir "$UNINSTALL_STAGE"
}

begin_uninstall() {
    [ ! -e "$UNINSTALL_STAGE" ] && [ ! -L "$UNINSTALL_STAGE" ] ||
        fail "uninstall recovery transaction already exists: $UNINSTALL_STAGE"
    mkdir "$UNINSTALL_STAGE"
    {
        printf 'rhizome-uninstall-v1\n'
        printf '%s\n' "$ROOT"
    } > "$UNINSTALL_STAGE/owner"
    mv "$ROOT" "$UNINSTALL_STAGE/root"
    recover_uninstall
}

# Every root-mutating action uses one lock beside, rather than inside, the
# deletable root. Paths and ownership are revalidated only after it is held.
acquire_transaction_lock
assert_state_outside_root
recover_uninstall
scavenge_root_staging

if [ "$ACTION" = uninstall ]; then
    if [ ! -e "$ROOT" ] && [ ! -L "$ROOT" ]; then
        printf 'Rhizome is not installed at %s; nothing was removed.\n' "$ROOT"
        exit 0
    fi
    require_owned_root
    if [ -e "$ROOT/transactions/rollback" ] || [ -L "$ROOT/transactions/rollback" ]; then
        finish_rollback
        require_owned_root
    fi
    # Re-resolve both paths and revalidate ownership/content immediately before quarantine.
    assert_state_outside_root
    require_owned_root
    printf 'Removing only the positively marked Rhizome installation:\n  %s\n' "$ROOT"
    printf 'Drafts and recovery data were preserved outside this installation at:\n  %s\n' "$STATE_PATH"
    begin_uninstall
    printf 'Rhizome installation removed. Shell, PATH, Git, tmux, and unrelated tools were unchanged.\n'
    exit 0
fi

if [ "$ACTION" = rollback ]; then
    require_owned_root
    ensure_transactions
    if [ -e "$ROOT/transactions/rollback" ] || [ -L "$ROOT/transactions/rollback" ]; then
        finish_rollback
        printf 'Completed an interrupted rollback.\n'
        print_launch
        exit 0
    fi
    current=$(revision_from_pointer current)
    previous=$(revision_from_pointer previous)
    [ "$current" != "$previous" ] || fail "current and previous point to the same revision"
    journal_temporary=$ROOT/transactions/.rollback.new.$$
    {
        printf 'rhizome-rollback-v1\n'
        printf '%s\n' "$previous"
        printf '%s\n' "$current"
    } > "$journal_temporary"
    mv -f "$journal_temporary" "$ROOT/transactions/rollback"
    finish_rollback
    printf 'Rolled back from %s to %s.\n' "${current#revisions/}" "${previous#revisions/}"
    printf 'Rollback changes only the launcher pointer and does not restart a running Rhizome process.\n'
    print_launch
    exit 0
fi

if [ -e "$ROOT" ] || [ -L "$ROOT" ]; then
    require_owned_root
    if [ -e "$ROOT/transactions/rollback" ] || [ -L "$ROOT/transactions/rollback" ]; then
        finish_rollback
        require_owned_root
    fi
fi

# uv source, index, credential, mirror, and TLS-bypass settings must not come from
# an ambient shell. Standard proxy and CA variables remain available deliberately.
ambient_uv=$(env | sed -n 's/^\(UV_[A-Za-z0-9_]*\)=.*/\1/p' | sort -u | tr '\n' ' ')
[ -z "$ambient_uv" ] || fail "refusing ambient UV_* configuration: $ambient_uv"

OS=$(uname -s)
MACHINE=$(uname -m)
case "$OS:$MACHINE" in
    Darwin:arm64|Darwin:aarch64)
        TRIPLE=aarch64-apple-darwin
        SHA256=66e37d91f839e12481d7b932a1eccbfe732560f42c1cfb89faddfa2454534ba8
        ;;
    Darwin:x86_64|Darwin:amd64)
        TRIPLE=x86_64-apple-darwin
        SHA256=0a4bc8fcde4974ea3560be21772aeecab600a6f43fa6e58169f9fa7b3b71d302
        ;;
    Linux:aarch64|Linux:arm64|Linux:x86_64|Linux:amd64)
        case "$MACHINE" in
            aarch64|arm64) ARCH=aarch64 ;;
            *) ARCH=x86_64 ;;
        esac
        if command -v getconf >/dev/null 2>&1 && getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
            LIBC=gnu
        elif command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -i musl >/dev/null 2>&1; then
            LIBC=musl
        else
            fail "unsupported Linux libc: could not identify glibc or musl"
        fi
        TRIPLE=$ARCH-unknown-linux-$LIBC
        case "$TRIPLE" in
            aarch64-unknown-linux-gnu) SHA256=f2ee1cde9aabb4c6e43bd3f341dadaf42189a54e001e521346dc31547310e284 ;;
            x86_64-unknown-linux-gnu) SHA256=6681d691eb7f9c00ac6a3af54252f7ab29ae72f0c8f95bdc7f9d1401c23ea868 ;;
            aarch64-unknown-linux-musl) SHA256=46647dc16cbb7d6700f762fdd7a67d220abe18570914732bc310adc91308d272 ;;
            x86_64-unknown-linux-musl) SHA256=64ddb5f1087649e3f75aa50d139aa4f36ddde728a5295a141e0fa9697bfb7b0f ;;
        esac
        ;;
    *) fail "unsupported platform: $OS $MACHINE (supported: macOS/Linux ARM64 or x86_64)" ;;
esac

ASSET=uv-$TRIPLE.tar.gz
UV_URL=https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$ASSET
printf 'Planned persistent effects (revision %s):\n' "$REVISION"
printf '  Installation root: %s\n' "$ROOT"
printf '  New isolated revision payload: %s/store/%s\n' "$ROOT" "$REVISION"
printf '  Verified private uv: %s (%s)\n' "$UV_VERSION" "$ASSET"
printf '  Managed Python: %s under that revision\n' "$PYTHON_VERSION"
printf '  Lock-derived runtime artifacts and the complete build backend closure are pinned\n'
printf '  Atomic current pointer; the prior current revision is retained as previous\n'
printf 'No shell profile, PATH, Git, tmux, or system installation is modified. No sudo is used.\n'

if [ -e "$ROOT" ] || [ -L "$ROOT" ]; then
    require_owned_root
    destination=$ROOT/revisions/$REVISION
    payload=$ROOT/store/$REVISION
    if [ -e "$destination" ] || [ -L "$destination" ]; then
        [ ! -L "$destination" ] && [ -d "$destination" ] ||
            fail "unsafe existing revision path: $destination"
        complete=$destination/.rhizome-revision
        payload_complete=$payload/.rhizome-revision
        if [ ! -L "$complete" ] && [ -f "$complete" ] &&
            [ "$(cat "$complete" 2>/dev/null || :)" = "$REVISION" ] &&
            [ ! -L "$payload" ] && [ -d "$payload" ] &&
            [ ! -L "$payload_complete" ] && [ -f "$payload_complete" ] &&
            [ "$(cat "$payload_complete" 2>/dev/null || :)" = "$REVISION" ] &&
            [ -x "$payload/bin/rhizome" ]; then
            existing_revision=1
        else
            fail "existing revision is not a positively marked usable Rhizome installation: $destination"
        fi
    else
        existing_revision=0
    fi
else
    existing_revision=0
fi

if [ "$existing_revision" -eq 0 ]; then
    TMP_WORK=$(mktemp -d "${TMPDIR:-/tmp}/rhizome-install.XXXXXX") || fail "cannot create temporary directory"
    ARCHIVE=$TMP_WORK/$ASSET
    EXTRACT=$TMP_WORK/extract
    RUNTIME_CONSTRAINTS=$TMP_WORK/install-runtime-constraints.txt
    BUILD_CONSTRAINTS=$TMP_WORK/install-build-constraints.txt
    PRIVATE_HOME=$TMP_WORK/home
    PRIVATE_CONFIG=$TMP_WORK/config
    PRIVATE_DATA=$TMP_WORK/data
    PRIVATE_CACHE=$TMP_WORK/cache
    PRIVATE_STATE=$TMP_WORK/state
    mkdir "$EXTRACT" "$PRIVATE_HOME" "$PRIVATE_CONFIG" "$PRIVATE_DATA" \
        "$PRIVATE_CACHE" "$PRIVATE_STATE"

    run_acquisition_environment() {
        env -i \
            PATH="$PATH" LC_ALL=C \
            HOME="$PRIVATE_HOME" XDG_CONFIG_HOME="$PRIVATE_CONFIG" \
            XDG_DATA_HOME="$PRIVATE_DATA" XDG_CACHE_HOME="$PRIVATE_CACHE" \
            XDG_STATE_HOME="$PRIVATE_STATE" \
            HTTP_PROXY="${HTTP_PROXY:-}" HTTPS_PROXY="${HTTPS_PROXY:-}" \
            ALL_PROXY="${ALL_PROXY:-}" NO_PROXY="${NO_PROXY:-}" \
            http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" \
            all_proxy="${all_proxy:-}" no_proxy="${no_proxy:-}" \
            SSL_CERT_FILE="${SSL_CERT_FILE:-}" SSL_CERT_DIR="${SSL_CERT_DIR:-}" \
            CURL_CA_BUNDLE="${CURL_CA_BUNDLE:-}" \
            REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-}" \
            "$@"
    }

    download_https() {
        url=$1
        output=$2
        case "$url" in https://*) ;; *) fail "refusing non-HTTPS download" ;; esac
        if downloader=$(command -v curl 2>/dev/null); then
            guarded_command run_acquisition_environment "$downloader" -q --fail --location \
                --proto '=https' --tlsv1.2 --output "$output" "$url" ||
                fail "failed to download a pinned HTTPS artifact"
        elif downloader=$(command -v wget 2>/dev/null); then
            guarded_command run_acquisition_environment "$downloader" --no-config \
                --no-netrc --no-hsts --https-only --secure-protocol=TLSv1_2 \
                -q -O "$output" "$url" ||
                fail "failed to download a pinned HTTPS artifact"
        else
            fail "curl or wget is required to download pinned HTTPS artifacts"
        fi
    }

    download_https "$UV_URL" "$ARCHIVE"
    download_https "$RAW_URL/$REVISION/scripts/install-runtime-constraints.txt" "$RUNTIME_CONSTRAINTS"
    download_https "$RAW_URL/$REVISION/scripts/install-build-constraints.txt" "$BUILD_CONSTRAINTS"
    [ -s "$RUNTIME_CONSTRAINTS" ] && [ -s "$BUILD_CONSTRAINTS" ] ||
        fail "pinned dependency constraints are missing or empty"

    if checksum=$(command -v sha256sum 2>/dev/null); then
        ACTUAL=$(guarded_command run_acquisition_environment "$checksum" "$ARCHIVE" | awk '{print $1}')
    elif checksum=$(command -v shasum 2>/dev/null); then
        ACTUAL=$(guarded_command run_acquisition_environment "$checksum" -a 256 "$ARCHIVE" | awk '{print $1}')
    else
        fail "sha256sum or shasum is required to verify the uv archive"
    fi
    [ "$ACTUAL" = "$SHA256" ] || fail "uv archive checksum mismatch; expected $SHA256, got $ACTUAL"
    tar_command=$(command -v tar 2>/dev/null) || fail "tar is required to extract verified uv"
    guarded_command run_acquisition_environment "$tar_command" -xzf "$ARCHIVE" -C "$EXTRACT" ||
        fail "could not extract verified uv archive"
    UV_SOURCE=$EXTRACT/uv-$TRIPLE/uv
    [ ! -L "$UV_SOURCE" ] && [ -f "$UV_SOURCE" ] && [ -x "$UV_SOURCE" ] ||
        fail "verified uv archive did not contain the expected $TRIPLE executable"

    if [ ! -e "$ROOT" ]; then
        parent=${ROOT%/*}
        [ -n "$parent" ] || parent=/
        mkdir -p "$parent"
        ROOT_STAGE=$parent/.rhizome-root.$$.staging
        [ ! -e "$ROOT_STAGE" ] && [ ! -L "$ROOT_STAGE" ] || fail "root staging path already exists"
        mkdir "$ROOT_STAGE"
        printf '%s\n' "$OWNER_MARKER" > "$ROOT_STAGE/.rhizome-install-root"
        mkdir "$ROOT_STAGE/revisions" "$ROOT_STAGE/store" "$ROOT_STAGE/bin" \
            "$ROOT_STAGE/transactions"
        mv "$ROOT_STAGE" "$ROOT"
        ROOT_STAGE=
        ROOT=$(physical_path "$ROOT") || fail "cannot resolve published installation root"
    else
        require_owned_root
        ensure_transactions
    fi

    ensure_transactions

    empty_owned_stage() {
        stage=$1
        [ ! -L "$stage" ] && [ -d "$stage" ] || return 1
        [ -z "$(ls -A "$stage" 2>/dev/null)" ]
    }

    REVISION_STAGE=$ROOT/store/$REVISION
    payload_ready=0
    if [ -e "$REVISION_STAGE" ] || [ -L "$REVISION_STAGE" ]; then
        installing=$REVISION_STAGE/.rhizome-installing
        complete=$REVISION_STAGE/.rhizome-revision
        if [ ! -L "$REVISION_STAGE" ] && [ -d "$REVISION_STAGE" ] &&
            [ ! -L "$complete" ] && [ -f "$complete" ] &&
            [ "$(cat "$complete" 2>/dev/null || :)" = "$REVISION" ] &&
            [ -x "$REVISION_STAGE/bin/rhizome" ]; then
            if [ -e "$installing" ] || [ -L "$installing" ]; then
                [ ! -L "$installing" ] && [ -f "$installing" ] &&
                    [ "$(sed -n '1p' "$installing" 2>/dev/null || :)" = "$REVISION" ] ||
                    fail "unsafe completed revision payload marker: $REVISION_STAGE"
                rm "$installing"
            fi
            payload_ready=1
        elif [ ! -L "$REVISION_STAGE" ] && [ -d "$REVISION_STAGE" ] &&
            [ ! -L "$installing" ] && [ -f "$installing" ] &&
            [ "$(sed -n '1p' "$installing" 2>/dev/null || :)" = "$REVISION" ]; then
            guarded_command rm -rf "$REVISION_STAGE"
        else
            fail "unsafe interrupted revision payload: $REVISION_STAGE"
        fi
    fi

    if [ "$payload_ready" -eq 0 ]; then
        payload_staging=$ROOT/store/.staging-$REVISION
        if [ -e "$payload_staging" ] || [ -L "$payload_staging" ]; then
            payload_marker=$payload_staging/.rhizome-installing
            if [ ! -L "$payload_staging" ] && [ -d "$payload_staging" ] &&
                [ ! -L "$payload_marker" ] && [ -f "$payload_marker" ] &&
                [ "$(sed -n '1p' "$payload_marker" 2>/dev/null || :)" = "$REVISION" ]; then
                guarded_command rm -rf "$payload_staging"
            elif empty_owned_stage "$payload_staging"; then
                guarded_command rm -rf "$payload_staging"
            else
                fail "unsafe revision payload staging path: $payload_staging"
            fi
        fi
        mkdir "$payload_staging"
        printf '%s\n%s\n' "$REVISION" "$$" > "$payload_staging/.rhizome-installing"
        mv "$payload_staging" "$REVISION_STAGE"
        mkdir "$REVISION_STAGE/uv" "$REVISION_STAGE/uv/bin" "$REVISION_STAGE/tools" \
            "$REVISION_STAGE/bin" "$REVISION_STAGE/python" "$REVISION_STAGE/python-bin" \
            "$REVISION_STAGE/cache" "$REVISION_STAGE/home" "$REVISION_STAGE/config" \
            "$REVISION_STAGE/data" "$REVISION_STAGE/xdg-cache" "$REVISION_STAGE/state"
        cp "$UV_SOURCE" "$REVISION_STAGE/uv/bin/uv"
        chmod 700 "$REVISION_STAGE/uv/bin/uv"
        cp "$RUNTIME_CONSTRAINTS" "$REVISION_STAGE/runtime-constraints.txt"
        cp "$BUILD_CONSTRAINTS" "$REVISION_STAGE/build-constraints.txt"

        UV=$REVISION_STAGE/uv/bin/uv
        run_private_uv() {
            guarded_command env -i \
                PATH="$PATH" LC_ALL=C \
                HOME="$REVISION_STAGE/home" XDG_CONFIG_HOME="$REVISION_STAGE/config" \
                XDG_DATA_HOME="$REVISION_STAGE/data" \
                XDG_CACHE_HOME="$REVISION_STAGE/xdg-cache" \
                XDG_STATE_HOME="$REVISION_STAGE/state" \
                HTTP_PROXY="${HTTP_PROXY:-}" HTTPS_PROXY="${HTTPS_PROXY:-}" \
                ALL_PROXY="${ALL_PROXY:-}" NO_PROXY="${NO_PROXY:-}" \
                http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" \
                all_proxy="${all_proxy:-}" no_proxy="${no_proxy:-}" \
                SSL_CERT_FILE="${SSL_CERT_FILE:-}" SSL_CERT_DIR="${SSL_CERT_DIR:-}" \
                CURL_CA_BUNDLE="${CURL_CA_BUNDLE:-}" \
                REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-}" \
                UV_TOOL_DIR="$REVISION_STAGE/tools" UV_TOOL_BIN_DIR="$REVISION_STAGE/bin" \
                UV_PYTHON_INSTALL_DIR="$REVISION_STAGE/python" \
                UV_PYTHON_BIN_DIR="$REVISION_STAGE/python-bin" \
                UV_CACHE_DIR="$REVISION_STAGE/cache" UV_NO_MODIFY_PATH=1 \
                UV_MANAGED_PYTHON=1 UV_NO_CONFIG=1 \
                GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false \
                GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_COUNT=2 \
                GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= \
                GIT_CONFIG_KEY_1=core.askPass GIT_CONFIG_VALUE_1=/bin/false \
                "$UV" "$@"
        }

        UV_OUTPUT=$(run_private_uv --version) || fail "verified uv executable could not run"
        case "$UV_OUTPUT" in
            "uv $UV_VERSION"*) ;;
            *) fail "verified uv executable reported an unexpected version: $UV_OUTPUT" ;;
        esac
        run_private_uv python install --managed-python --no-config --no-bin "$PYTHON_VERSION"
        # uv 0.11.7 splits constraint arguments on spaces even when shell-quoted.
        # Resolve simple relative filenames from the positively owned payload.
        run_private_uv --directory "$REVISION_STAGE" tool install \
            --python "$PYTHON_VERSION" --managed-python \
            --no-python-downloads --no-config --no-sources \
            --default-index https://pypi.org/simple --index-strategy first-index \
            --keyring-provider disabled --constraints runtime-constraints.txt \
            --build-constraints build-constraints.txt \
            "git+$PROJECT_URL.git@$REVISION"
        [ -x "$REVISION_STAGE/bin/rhizome" ] || fail "uv did not install the rhizome executable"
        printf '%s\n' "$REVISION" > "$REVISION_STAGE/.rhizome-revision"
        rm "$REVISION_STAGE/.rhizome-installing"
    fi

    PUBLICATION_STAGE=$ROOT/revisions/.staging-$REVISION
    if [ -e "$PUBLICATION_STAGE" ] || [ -L "$PUBLICATION_STAGE" ]; then
        publication_marker=$PUBLICATION_STAGE/.rhizome-revision
        if [ ! -L "$PUBLICATION_STAGE" ] && [ -d "$PUBLICATION_STAGE" ] &&
            [ ! -L "$publication_marker" ] && [ -f "$publication_marker" ] &&
            [ "$(cat "$publication_marker" 2>/dev/null || :)" = "$REVISION" ]; then
            guarded_command rm -rf "$PUBLICATION_STAGE"
        elif empty_owned_stage "$PUBLICATION_STAGE"; then
            guarded_command rm -rf "$PUBLICATION_STAGE"
        else
            fail "unsafe revision publication staging path: $PUBLICATION_STAGE"
        fi
    fi
    mkdir "$PUBLICATION_STAGE"
    printf '%s\n' "$REVISION" > "$PUBLICATION_STAGE/.rhizome-revision"
    mv "$PUBLICATION_STAGE" "$ROOT/revisions/$REVISION"
    PUBLICATION_STAGE=
    REVISION_STAGE=
fi

# The sole stable public launcher is app-owned. A wrapper from an earlier managed
# installation is removed only when the launcher directory is positively marked.
bin_marker=$ROOT/bin/.rhizome-owned
launcher=$ROOT/bin/rhizome
legacy_uv_launcher=$ROOT/bin/uv
if [ -e "$launcher" ] || [ -L "$launcher" ] || [ -e "$legacy_uv_launcher" ] ||
    [ -L "$legacy_uv_launcher" ] || [ -e "$bin_marker" ] || [ -L "$bin_marker" ]; then
    [ ! -L "$bin_marker" ] && [ -f "$bin_marker" ] &&
        [ "$(cat "$bin_marker" 2>/dev/null || :)" = "$OWNER_MARKER" ] ||
        fail "refusing to overwrite unmarked launchers at $ROOT/bin"
    for candidate in "$launcher" "$legacy_uv_launcher"; do
        [ ! -L "$candidate" ] && { [ ! -e "$candidate" ] || [ -f "$candidate" ]; } ||
            fail "refusing unsafe launcher at $candidate"
    done
fi
if [ -e "$legacy_uv_launcher" ]; then
    rm "$legacy_uv_launcher"
fi
printf '%s\n' "$OWNER_MARKER" > "$bin_marker"
ensure_transactions
launcher_temporary=$ROOT/transactions/.rhizome-launcher.new.$$
cat > "$launcher_temporary" <<'LAUNCHER'
#!/bin/sh
set -eu
root=$(CDPATH= cd "$(dirname "$0")/.." && pwd -P)
[ ! -L "$root/current" ] && [ -f "$root/current" ] || {
    echo "rhizome: installation has no safe current pointer" >&2
    exit 1
}
target=$(cat "$root/current")
revision=${target#revisions/}
case "$target:$revision" in
    revisions/*:????????????????????????????????????????) ;;
    *) echo "rhizome: installation has no safe current pointer" >&2; exit 1 ;;
esac
case "$revision" in
    *[!0-9a-f]*) echo "rhizome: installation has no safe current pointer" >&2; exit 1 ;;
esac
record=$root/$target
[ ! -L "$record" ] && [ -d "$record" ] &&
    [ ! -L "$record/.rhizome-revision" ] &&
    [ "$(cat "$record/.rhizome-revision" 2>/dev/null || :)" = "$revision" ] || {
    echo "rhizome: current revision is not marked" >&2
    exit 1
}
revision_root=$root/store/$revision
[ ! -L "$revision_root" ] && [ -d "$revision_root" ] &&
    [ ! -L "$revision_root/.rhizome-revision" ] &&
    [ "$(cat "$revision_root/.rhizome-revision" 2>/dev/null || :)" = "$revision" ] || {
    echo "rhizome: installation has no usable current revision" >&2
    exit 1
}
[ -x "$revision_root/bin/rhizome" ] || {
    echo "rhizome: current revision has no executable" >&2
    exit 1
}
exec "$revision_root/bin/rhizome" "$@"
LAUNCHER
chmod 700 "$launcher_temporary"
mv -f "$launcher_temporary" "$launcher"

if [ -e "$ROOT/current" ] || [ -L "$ROOT/current" ]; then
    old_current=$(revision_from_pointer current)
else
    old_current=
fi
if [ -e "$ROOT/previous" ] || [ -L "$ROOT/previous" ]; then
    revision_from_pointer previous >/dev/null
fi
new_target=revisions/$REVISION
if [ -n "$old_current" ] && [ "$old_current" != "$new_target" ]; then
    atomic_pointer previous "$old_current"
fi
atomic_pointer current "$new_target"
printf 'Installed and selected Rhizome commit %s.\n' "$REVISION"
print_launch
printf '\nDirect uv alternative (using an independently installed uv 0.11.7):\n'
printf '  Use managed CPython %s and the pinned runtime/build constraints shipped at commit %s.\n' \
    "$PYTHON_VERSION" "$REVISION"
printf 'Rollback: %s --rollback\nUninstall: %s --uninstall\n' "$0" "$0"
