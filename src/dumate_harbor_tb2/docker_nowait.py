from __future__ import annotations

import asyncio
import os
from pathlib import Path

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.trial.paths import EnvironmentPaths


class DockerNoWaitEnvironment(DockerEnvironment):
    """Docker environment tweaks for local Harbor TB2 runs on this Mac.

    Docker Compose v5 hangs on `up --wait` here, and many TB2 verifier scripts
    fetch uv at verification time.  Preloading uv keeps verifier failures from
    being caused by transient astral.sh download timeouts.
    """

    _UV_IMAGE = os.getenv("DUMATE_HARBOR_UV_IMAGE", "ghcr.io/astral-sh/uv:0.9.5")
    _UV_CACHE_ROOT = Path(__file__).resolve().parents[2] / ".cache" / "uv-bin" / "0.9.5"
    _UV_CACHE_LOCK: asyncio.Lock = asyncio.Lock()

    async def start(self, force_build: bool):
        if self._mounts_json:
            self._mounts_compose_path = self._write_mounts_compose_file()

        self._use_prebuilt = not force_build and self.task_env_config.docker_image

        if not self._use_prebuilt:
            lock = self._image_build_locks.setdefault(
                self.environment_name,
                asyncio.Lock(),
            )
            async with lock:
                await self._run_docker_compose_command(["build"])

        try:
            await self._run_docker_compose_command(["down", "--remove-orphans"])
        except RuntimeError:
            pass

        await self._run_docker_compose_command(["up", "--detach"])

        await self.exec(
            f"chmod 777 {EnvironmentPaths.agent_dir} {EnvironmentPaths.verifier_dir}"
        )
        await self._preload_uv_for_verifier()

    async def _preload_uv_for_verifier(self) -> None:
        platform = await self._detect_container_platform()
        uv_path, uvx_path = await self._ensure_host_uv_binaries(platform)
        await self.upload_file(uv_path, "/usr/local/bin/uv")
        await self.upload_file(uvx_path, "/usr/local/bin/uvx")

        result = await self.exec(
            r"""
set -eu
chmod 755 /usr/local/bin/uv /usr/local/bin/uvx
mkdir -p /root/.local/bin
ln -sf /usr/local/bin/uv /root/.local/bin/uv
ln -sf /usr/local/bin/uvx /root/.local/bin/uvx
cat > /root/.local/bin/env <<'EOF'
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
EOF
chmod 755 /root/.local/bin/env
cat > /usr/local/bin/curl <<'EOF'
#!/usr/bin/env bash
set -e

for arg in "$@"; do
  case "$arg" in
    https://astral.sh/uv/*/install.sh|https://releases.astral.sh/*/uv-installer.sh)
      cat <<'INSTALL'
#!/usr/bin/env sh
set -eu
mkdir -p "$HOME/.local/bin"
ln -sf /usr/local/bin/uv "$HOME/.local/bin/uv"
ln -sf /usr/local/bin/uvx "$HOME/.local/bin/uvx"
cat > "$HOME/.local/bin/env" <<'ENVEOF'
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ENVEOF
INSTALL
      exit 0
      ;;
  esac
done

exec /usr/bin/curl "$@"
EOF
chmod 755 /usr/local/bin/curl
/usr/local/bin/uv --version
/usr/local/bin/uvx --version
""",
            timeout_sec=60,
            user="root",
        )
        if result.return_code != 0:
            raise RuntimeError(
                "Failed to preload uv into task container. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )

    async def _detect_container_platform(self) -> str:
        result = await self.exec("uname -m", timeout_sec=30, user="root")
        if result.return_code != 0:
            raise RuntimeError(
                "Failed to detect task container architecture. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )

        machine = result.stdout.strip().splitlines()[-1]
        if machine in {"x86_64", "amd64"}:
            return "linux/amd64"
        if machine in {"aarch64", "arm64"}:
            return "linux/arm64"
        raise RuntimeError(f"Unsupported task container architecture: {machine!r}")

    @classmethod
    async def _ensure_host_uv_binaries(cls, platform: str) -> tuple[Path, Path]:
        async with cls._UV_CACHE_LOCK:
            cache_dir = cls._UV_CACHE_ROOT / platform.replace("/", "-")
            cache_dir.mkdir(parents=True, exist_ok=True)
            uv_path = cache_dir / "uv"
            uvx_path = cache_dir / "uvx"
            if uv_path.exists() and uvx_path.exists():
                return uv_path, uvx_path

            await cls._run_host_command(
                ["docker", "pull", "--platform", platform, cls._UV_IMAGE],
                timeout_sec=300,
            )
            container_id = (
                await cls._run_host_command(
                    ["docker", "create", "--platform", platform, cls._UV_IMAGE]
                )
            ).strip()
            try:
                await cls._run_host_command(
                    ["docker", "cp", f"{container_id}:/uv", str(uv_path)]
                )
                await cls._run_host_command(
                    ["docker", "cp", f"{container_id}:/uvx", str(uvx_path)]
                )
            finally:
                await cls._run_host_command(["docker", "rm", container_id], check=False)

            os.chmod(uv_path, 0o755)
            os.chmod(uvx_path, 0o755)
            return uv_path, uvx_path

    @staticmethod
    async def _run_host_command(
        command: list[str],
        *,
        check: bool = True,
        timeout_sec: int = 120,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout_bytes = b""
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_sec,
            )
        except TimeoutError:
            process.kill()
            stdout_bytes, _ = await process.communicate()
            raise RuntimeError(f"Command timed out after {timeout_sec}s: {command}")

        stdout = stdout_bytes.decode(errors="replace")
        if check and process.returncode != 0:
            raise RuntimeError(
                f"Command failed ({process.returncode}): {' '.join(command)}\n{stdout}"
            )
        return stdout
