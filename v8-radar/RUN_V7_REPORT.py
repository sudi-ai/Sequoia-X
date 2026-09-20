from v7.reporting import write_comparison_report

if __name__ == "__main__":
    path = write_comparison_report()
    print("V6 vs V7 对比报告已生成：", path)
