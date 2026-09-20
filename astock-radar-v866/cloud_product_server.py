"""Cloud entry point; retain existing account, portfolio and HTTP routes."""
import browser_dashboard_focus as workbench
from cloud_product_workbench import install
from decision_workspace import install as install_decision_workspace
from fusion_workspace_v2 import install as install_fusion_workspace
from fusion_ai_workspace import install as install_ai_workspace

install(workbench)
install_decision_workspace()
install_fusion_workspace()
install_ai_workspace()

if __name__ == "__main__":
    workbench.main()
