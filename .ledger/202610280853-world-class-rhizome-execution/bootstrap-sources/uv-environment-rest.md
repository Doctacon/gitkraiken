# Environment variables | uv

not be applied, uv exits with an error without running the command. Only supported on Unix.

### [`UV_S3_ENDPOINT_URL`](#uv_s3_endpoint_url)

added in `0.8.21`

The URL to treat as an S3-compatible storage endpoint. Requests to this endpoint will be signed using AWS Signature Version 4 based on the `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, and `AWS_CONFIG_FILE` environment variables.

### [`UV_SKIP_WHEEL_FILENAME_CHECK`](#uv_skip_wheel_filename_check)

added in `0.8.23`

Avoid verifying that wheel filenames match their contents when installing wheels. This is not recommended, as wheels with inconsistent filenames should be considered invalid and corrected by the relevant package maintainers; however, this option can be used to work around invalid artifacts in rare cases.

### [`UV_STACK_SIZE`](#uv_stack_size)

added in `0.0.5`

Use to set the stack size used by uv.

The value is in bytes, and if both `UV_STACK_SIZE` are `RUST_MIN_STACK` unset, uv uses a 4MB (4194304) stack. `UV_STACK_SIZE` takes precedence over `RUST_MIN_STACK`.

Unlike the normal `RUST_MIN_STACK` semantics, this can affect main thread stack size, because we actually spawn our own main2 thread to work around the fact that Windows' real main thread is only 1MB. That thread has size `max(UV_STACK_SIZE, 1MB)`.

### [`UV_SYSTEM_CERTS`](#uv_system_certs)

added in `0.11.0`

Equivalent to the `--system-certs` command-line argument. If set to `true`, uv will load TLS certificates from the platform's native certificate store instead of the bundled Mozilla root certificates.

### [`UV_SYSTEM_PYTHON`](#uv_system_python)

added in `0.1.18`

Equivalent to the `--system` command-line argument. If set to `true`, uv will use the first Python interpreter found in the system `PATH`.

WARNING: `UV_SYSTEM_PYTHON=true` is intended for use in continuous integration (CI) or containerized environments and should be used with caution, as modifying the system Python can lead to unexpected behavior.

### [`UV_TOOL_BIN_DIR`](#uv_tool_bin_dir)

added in `0.3.0`

Specifies the "bin" directory for installing tool executables.

### [`UV_TOOL_DIR`](#uv_tool_dir)

added in `0.2.16`

Specifies the directory where uv stores managed tools.

### [`UV_TORCH_BACKEND`](#uv_torch_backend)

added in `0.6.9`

Equivalent to the `--torch-backend` command-line argument (e.g., `cpu`, `cu126`, or `auto`).

### [`UV_UNMANAGED_INSTALL`](#uv_unmanaged_install)

added in `0.5.0`

Used ephemeral environments like CI to install uv to a specific path while preventing the installer from modifying shell profiles or environment variables.

### [`UV_UPLOAD_HTTP_TIMEOUT`](#uv_upload_http_timeout)

added in `0.9.1`

Timeout (in seconds) for only upload HTTP requests. (default: 900 s)

### [`UV_VENV_CLEAR`](#uv_venv_clear)

added in `0.8.0`

Equivalent to the `--clear` command-line argument. If set, uv will remove any existing files or directories at the target path.

### [`UV_VENV_RELOCATABLE`](#uv_venv_relocatable)

added in `0.10.8`

Equivalent to the `--relocatable` command-line argument. If set, the virtual environment will be relocatable.

### [`UV_VENV_SEED`](#uv_venv_seed)

added in `0.5.21`

Install seed packages (one or more of: `pip`, `setuptools`, and `wheel`) into the virtual environment created by `uv venv`.

Note that `setuptools` and `wheel` are not included in Python 3.12+ environments.

### [`UV_WORKING_DIR`](#uv_working_dir)

added in `0.9.14`

Equivalent to the `--directory` command-line argument. `UV_WORKING_DIRECTORY` (added in v0.9.1) is also supported for backwards compatibility.

## [Externally defined variables](#externally-defined-variables)

uv also reads the following externally defined environment variables:

### [`ALL_PROXY`](#all_proxy)

added in `0.1.38`

General proxy for all network requests.

### [`ANDROID_API_LEVEL`](#android_api_level)

added in `0.8.16`

Used with `--python-platform aarch64-linux-android` and related variants to set the Android API level. (i.e., the minimum supported Android API level).

Defaults to `24`.

### [`APPDATA`](#appdata)

added in `0.1.42`

Path to user-level configuration directory on Windows systems.

### [`AWS_ACCESS_KEY_ID`](#aws_access_key_id)

added in `0.8.21`

The AWS access key ID to use when signing S3 requests.

### [`AWS_CONFIG_FILE`](#aws_config_file)

added in `0.8.21`

The AWS config file to use when signing S3 requests.

### [`AWS_DEFAULT_REGION`](#aws_default_region)

added in `0.8.21`

The default AWS region to use when signing S3 requests, if `AWS_REGION` is not set.

### [`AWS_PROFILE`](#aws_profile)

added in `0.8.21`

The AWS profile to use when signing S3 requests.

### [`AWS_REGION`](#aws_region)

added in `0.8.21`

The AWS region to use when signing S3 requests.

### [`AWS_SECRET_ACCESS_KEY`](#aws_secret_access_key)

added in `0.8.21`

The AWS secret access key to use when signing S3 requests.

### [`AWS_SESSION_TOKEN`](#aws_session_token)

added in `0.8.21`

The AWS session token to use when signing S3 requests.

### [`AWS_SHARED_CREDENTIALS_FILE`](#aws_shared_credentials_file)

added in `0.8.21`

The AWS shared credentials file to use when signing S3 requests.

### [`BASH_VERSION`](#bash_version)

added in `0.1.28`

Used to detect Bash shell usage.

### [`CLICOLOR_FORCE`](#clicolor_force)

added in `0.1.32`

Use to control color via `anstyle`.

### [`COLUMNS`](#columns)

added in `0.6.2`

Overrides terminal width used for wrapping. This variable is not read by uv directly.

This is a quasi-standard variable, described, e.g., in `ncurses(3x)`.

### [`CONDA_DEFAULT_ENV`](#conda_default_env)

added in `0.5.0`

Used to determine the name of the active Conda environment.

### [`CONDA_PREFIX`](#conda_prefix)

added in `0.0.5`

Used to detect the path of an active Conda environment.

### [`DEPENDABOT`](#dependabot)

added in `0.9.11`

Used to determine if we're running in Dependabot.

### [`FISH_VERSION`](#fish_version)

added in `0.1.28`

Used to detect Fish shell usage.

### [`FORCE_COLOR`](#force_color)

added in `0.2.7`

Forces colored output regardless of terminal support.

See [force-color.org](https://force-color.org).

### [`GITHUB_ACTIONS`](#github_actions)

added in `0.4.16`

Indicates that the current process is running in GitHub Actions.

`uv publish` may attempt trusted publishing flows when set to `true`.

### [`GITLAB_CI`](#gitlab_ci)

added in `0.8.18`

Indicates that the current process is running in GitLab CI.

`uv publish` may attempt trusted publishing flows when set to `true`.

### [`HF_TOKEN`](#hf_token)

added in `0.8.1`

Authentication token for Hugging Face requests. When set, uv will use this token when making requests to `https://huggingface.co/` and any subdomains.

### [`HOME`](#home)

added in `0.0.5`

The standard `HOME` env var.

### [`HTTPS_PROXY`](#https_proxy)

added in `0.1.38`

Proxy for HTTPS requests.

### [`HTTP_PROXY`](#http_proxy)

added in `0.1.38`

Proxy for HTTP requests.

### [`HTTP_TIMEOUT`](#http_timeout)

added in `0.1.7`

Timeout (in seconds) for HTTP requests. Equivalent to `UV_HTTP_TIMEOUT`.

### [`IPHONEOS_DEPLOYMENT_TARGET`](#iphoneos_deployment_target)

added in `0.8.16`

Used with `--python-platform arm64-apple-ios` and related variants to set the deployment target (i.e., the minimum supported iOS version).

Defaults to `13.0`.

### [`JPY_SESSION_NAME`](#jpy_session_name)

added in `0.2.6`

Used to detect when running inside a Jupyter notebook.

### [`KSH_VERSION`](#ksh_version)

added in `0.2.33`

Used to detect Ksh shell usage.

### [`LOCALAPPDATA`](#localappdata)

added in `0.3.3`

Used to look for Microsoft Store Pythons installations.

### [`MACOSX_DEPLOYMENT_TARGET`](#macosx_deployment_target)

added in `0.1.42`

Used with `--python-platform macos` and related variants to set the deployment target (i.e., the minimum supported macOS version).

Defaults to `13.0`, the least-recent non-EOL macOS version at time of writing.

### [`NETRC`](#netrc)

added in `0.1.16`

Use to set the .netrc file location.

### [`NO_COLOR`](#no_color)

added in `0.2.7`

Disables colored output (takes precedence over `FORCE_COLOR`).

See [no-color.org](https://no-color.org).

### [`NO_PROXY`](#no_proxy)

added in `0.1.38`

Comma-separated list of hostnames (e.g., `example.com`) and/or patterns (e.g., `192.168.1.0/24`) that should bypass the proxy.

### [`NU_VERSION`](#nu_version)

added in `0.1.16`

Used to detect `NuShell` usage.

added in `0.4.18`

The standard `PAGER` posix env var. Used by `uv` to configure the appropriate pager.

### [`PATH`](#path)

added in `0.0.5`

The standard `PATH` env var.

### [`PROMPT`](#prompt)

added in `0.1.16`

Used to detect the use of the Windows Command Prompt (as opposed to PowerShell).

### [`PSModulePath`](#psmodulepath)

added in `0.10.0`

Used to detect PowerShell usage (set by PowerShell on all platforms).

### [`PWD`](#pwd)

added in `0.0.5`

The standard `PWD` posix env var.

### [`PYC_INVALIDATION_MODE`](#pyc_invalidation_mode)

added in `0.1.7`

The validation modes to use when run with `--compile`.

See [`PycInvalidationMode`](https://docs.python.org/3/library/py_compile.html#py_compile.PycInvalidationMode).

### [`PYTHONPATH`](#pythonpath)

added in `0.1.22`

Adds directories to Python module search path (e.g., `PYTHONPATH=/path/to/modules`).

### [`PYX_API_KEY`](#pyx_api_key)

added in `0.8.15`

The pyx API key (e.g., `sk-pyx-...`).

### [`PYX_AUTH_TOKEN`](#pyx_auth_token)

added in `0.8.15`

The pyx authentication token (e.g., `eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...`), as output by `uv auth token`.

### [`RUFF`](#ruff)

added in `0.11.22`

The path to the Ruff binary used by `uv format`.

### [`RUST_BACKTRACE`](#rust_backtrace)

added in `0.7.22`

If set, it can be used to display more stack trace details when a panic occurs. This is used by uv particularly on windows to show more details during a platform exception.

For example:

*   `RUST_BACKTRACE=1` will print a short backtrace.
*   `RUST_BACKTRACE=full` will print a full backtrace.

See the [Rust backtrace documentation](https://doc.rust-lang.org/std/backtrace/index.html) for more.

### [`RUST_LOG`](#rust_log)

added in `0.0.5`

If set, uv will use this value as the log level for its `--verbose` output. Accepts any filter compatible with the `tracing_subscriber` crate.

For example:

*   `RUST_LOG=uv=debug` is the equivalent of adding `--verbose` to the command line
*   `RUST_LOG=trace` will enable trace-level logging.

See the [tracing documentation](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/filter/struct.EnvFilter.html#example-syntax) for more.

### [`RUST_MIN_STACK`](#rust_min_stack)

added in `0.5.19`

Use to set the stack size used by uv.

The value is in bytes, and if both `UV_STACK_SIZE` are `RUST_MIN_STACK` unset, uv uses a 4MB (4194304) stack. `UV_STACK_SIZE` takes precedence over `RUST_MIN_STACK`.

Prefer setting `UV_STACK_SIZE`, since `RUST_MIN_STACK` also affects subprocesses, such as build backends that use Rust code.

Unlike the normal `RUST_MIN_STACK` semantics, this can affect main thread stack size, because we actually spawn our own main2 thread to work around the fact that Windows' real main thread is only 1MB. That thread has size `max(RUST_MIN_STACK, 1MB)`.

### [`SHELL`](#shell)

added in `0.1.16`

The standard `SHELL` posix env var.

### [`SSL_CERT_DIR`](#ssl_cert_dir)

added in `0.9.10`

Path to a directory containing PEM-encoded CA certificate files for TLS connections.

Multiple entries are supported, separated using a platform-specific delimiter (`:` on Unix, `;` on Windows).

Certificates are usually stored with `.pem`, `.crt`, or `.cer` extensions, but uv will attempt to read a certificate from any regular file in the provided `SSL_CERT_DIR`.

Files that cannot be parsed as PEM certificates are ignored. uv resolves symlinks and ignores dangling symlinks.

Only PEM-encoded files are supported, i.e., DER-encoded files are not supported.

When set, this overrides the default certificate source (bundled Mozilla roots or system certificates). Only the certificates in this directory will be trusted.

### [`SSL_CERT_FILE`](#ssl_cert_file)

added in `0.1.14`

Path to a CA certificate bundle file for TLS connections.

Requires a PEM-encoded certificate file (e.g., `certs.pem`, `ca-bundle.crt`). DER-encoded files are not supported.

When set, this overrides the default certificate source (bundled Mozilla roots or system certificates). Only the certificates in this file will be trusted.

### [`SSL_CLIENT_CERT`](#ssl_client_cert)

added in `0.2.11`

If set, uv will use this file for mTLS authentication. This should be a single file containing both the certificate and the private key in PEM format.

### [`SYSTEMDRIVE`](#systemdrive)

added in `0.4.26`

Path to system-level configuration directory on Windows systems.

### [`TRACING_DURATIONS_FILE`](#tracing_durations_file)

added in `0.0.5`

Use to create the tracing durations file via the `tracing-durations-export` feature.

### [`TY`](#ty)

added in `0.11.22`

The path to the ty binary used by `uv check`.

### [`USERPROFILE`](#userprofile)

added in `0.0.5`

Path to root directory of user's profile on Windows systems.

### [`UV`](#uv)

added in `0.6.0`

The path to the binary that was used to invoke uv.

This is propagated to all subprocesses spawned by uv.

If the executable was invoked through a symbolic link, some platforms will return the path of the symbolic link and other platforms will return the path of the symbolic link’s target.

See [https://doc.rust-lang.org/std/env/fn.current\_exe.html#security](https://doc.rust-lang.org/std/env/fn.current_exe.html#security) for security considerations.

### [`VIRTUAL_ENV`](#virtual_env)

added in `0.0.5`

Used to detect an activated virtual environment.

### [`VIRTUAL_ENV_DISABLE_PROMPT`](#virtual_env_disable_prompt)

added in `0.0.5`

If set to `1` before a virtual environment is activated, then the virtual environment name will not be prepended to the terminal prompt.

### [`XDG_BIN_HOME`](#xdg_bin_home)

added in `0.2.16`

Path to directory where executables are installed.

### [`XDG_CACHE_HOME`](#xdg_cache_home)

added in `0.1.17`

Path to cache directory on Unix systems.

### [`XDG_CONFIG_DIRS`](#xdg_config_dirs)

added in `0.4.26`

Path to system-level configuration directory on Unix systems.

### [`XDG_CONFIG_HOME`](#xdg_config_home)

added in `0.1.34`

Path to user-level configuration directory on Unix systems.

### [`XDG_DATA_HOME`](#xdg_data_home)

added in `0.2.16`

Path to directory for storing managed Python installations and tools.

### [`ZDOTDIR`](#zdotdir)

added in `0.2.25`

Used to determine which `.zshenv` to use when Zsh is being used.

### [`ZSH_VERSION`](#zsh_version)

added in `0.1.28`

Used to detect Zsh shell usage.

### [`_CONDA_ROOT`](#_conda_root)

added in `0.8.18`

Used to determine the root install path of Conda.

---
Showing chars 28000-42915 of 42915.