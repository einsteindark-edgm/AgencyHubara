import subprocess
print(subprocess.run(['which', 'docker'], capture_output=True, text=True).stdout)
print(subprocess.run(['which', 'podman'], capture_output=True, text=True).stdout)
print(subprocess.run(['ls', '-la', '/usr/local/bin'], capture_output=True, text=True).stdout)
