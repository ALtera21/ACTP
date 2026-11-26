import os
import tempfile

os.environ["ACTP_HOME"] = tempfile.mkdtemp(prefix="actp-test-")