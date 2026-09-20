from v8.env_loader import load_v8_env
load_v8_env()
from v8.forward_runtime import settle_pending
if __name__=='__main__':print(settle_pending())
