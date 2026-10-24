import ast
import gzip
import os
import re
import subprocess
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path

from fck_rkn.remote import render


class ScriptPlatformTests(unittest.TestCase):
    def test_scripts_do_not_reference_a_cloud_provider(self) -> None:
        forbidden = ("google", "gcloud", "metadata", "gcp", "iap", "aws", "azure")
        for path in (files("fck_rkn.remote") / "scripts").iterdir():
            if path.is_dir() or path.name.endswith(".pyc"):
                continue
            text = path.read_text().lower()
            for word in forbidden:
                self.assertNotIn(word, text, f"{path.name} mentions {word}")


class LoadOutlineImageTests(unittest.TestCase):
    def test_loads_archive_from_stdin_only_when_image_is_missing(self) -> None:
        script = render(
            "load_outline_image.sh",
            image="img:tag",
            archive_size=12345,
        )
        self.assertIn("expected_image=img:tag", script)
        self.assertLess(
            script.index("docker image inspect"), script.index("docker load")
        )
        self.assertIn("skipping the load", script)
        self.assertIn("python3 -c", script)
        self.assertIn("12345 | gunzip | sudo docker load", script)
        self.assertIn("processed * 100 // total", script)
        self.assertIn("reported + 5", script)
        self.assertIn("min(99,", script)
        self.assertIn("image: 100%", script)
        self.assertNotIn("docker build", script)

    def test_load_script_reports_percentage_progress(self) -> None:
        archive = gzip.compress(os.urandom(300_000))
        script = render(
            "load_outline_image.sh",
            image="img:tag",
            archive_size=len(archive),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sudo = root / "sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = docker ] && [ "$2" = image ]; then exit 1; fi\n'
                'if [ "$1" = docker ] && [ "$2" = load ]; then '
                'cat > "$LOAD_CAPTURE"; exit; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            sudo.chmod(0o755)
            docker = root / "docker"
            docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            docker.chmod(0o755)
            curl = root / "curl"
            curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            curl.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "LOAD_CAPTURE": str(root / "loaded-image.tar"),
            }
            result = subprocess.run(
                ["bash", "-c", script],
                input=archive,
                env=environment,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                Path(environment["LOAD_CAPTURE"]).read_bytes(),
                gzip.decompress(archive),
            )
            progress = result.stderr.decode()
            percentages = [
                int(value)
                for value in re.findall(r"image: (\d+)%", progress)
            ]
            self.assertGreaterEqual(len(percentages), 4)
            self.assertEqual(percentages, sorted(set(percentages)))
            self.assertTrue(all(value % 5 == 0 for value in percentages))
            self.assertEqual(percentages[-1], 95)
            self.assertIn("image: 100%", result.stdout.decode())


class RenderTests(unittest.TestCase):
    def test_fills_placeholders_and_expands_includes(self) -> None:
        script = render("deploy_squid.sh", container_name="c", image="i", port=1, config_directory="d")
        self.assertIn("container_name=c", script)
        self.assertIn("apt-get install -y docker.io curl", script)
        self.assertNotIn("#include", script)
        self.assertNotIn("#!", script)

    def test_rejects_missing_and_unused_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "no value for @IMAGE@"):
            render("deploy_squid.sh", container_name="c", port=1, config_directory="d")
        with self.assertRaisesRegex(ValueError, "does not use: EXTRA"):
            render("deploy_squid.sh", container_name="c", image="i", port=1, config_directory="d", extra="x")

    def test_values_are_not_expanded_twice(self) -> None:
        script = render("deploy_squid.sh", container_name="@IMAGE@", image="i", port=1, config_directory="d")
        self.assertIn("container_name=@IMAGE@", script)

    def test_python_scripts_are_valid_python(self) -> None:
        for name in (
            "create_outline_user.py",
            "squid_http_check.py",
        ):
            source = (files("fck_rkn.remote") / "scripts" / name).read_text()
            ast.parse(source)
        ast.parse((files("fck_rkn") / "agents/rkn_blacklist.py").read_text())

    def test_shell_scripts_are_valid_bash(self) -> None:
        scripts = files("fck_rkn.remote") / "scripts"
        for entry in scripts.iterdir():
            if entry.name.endswith(".sh") and entry.name != "docker_setup.sh":
                with self.subTest(script=entry.name):
                    text = entry.read_text()
                    text = text.replace("#include docker_setup.sh", "true")
                    for include in (
                        "create_outline_user.py",
                        "squid_http_check.py",
                        "agents/rkn_blacklist.py",
                        "outline_shadowbox.Dockerfile",
                    ):
                        text = text.replace(f"#include {include}", "")
                    result = subprocess.run(
                        ["bash", "-n"], input=text, text=True, capture_output=True
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
