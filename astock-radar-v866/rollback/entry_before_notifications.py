import atexit,subprocess,sys,threading
from pathlib import Path
import cloud_product_server
stop=threading.Event()
children=[]
def supervise():
 while not stop.is_set():
  try:
   child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('fusion_runtime_v2.py')),'--loop'],stdin=subprocess.DEVNULL)
   children.append(child)
   child.wait()
   children.remove(child)
  except Exception as exc:print('FUSION_SUPERVISOR',type(exc).__name__,flush=True)
  if stop.wait(30):return
def shutdown():
 stop.set()
 for child in list(children):
  if child.poll() is None:child.terminate()
if __name__=='__main__':
 atexit.register(shutdown)
 threading.Thread(target=supervise,daemon=True).start()
 cloud_product_server.workbench.main()
