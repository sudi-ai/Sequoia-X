from v8.env_loader import load_v8_env
load_v8_env()
from v8.dashboard import write_markdown
if __name__=='__main__':print(write_markdown())
