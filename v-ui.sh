#!/bin/bash

# V-UI 管理脚本
# 快捷指令: v-ui

red='\033[0;31m'
green='\033[0;32m'
yellow='\033[0;33m'
plain='\033[0m'

# check root
[[ $EUID -ne 0 ]] && echo -e "${red}错误: ${plain} 必须使用root用户运行此脚本！\n" && exit 1

check_status() {
    if systemctl is-active v-ui &>/dev/null; then
        echo -e "当前状态: ${green}已启动${plain}"
        # Show memory usage if possible
        pid=$(pgrep -f "main.py" | head -n 1)
        if [[ -n "$pid" ]]; then
            echo -e "进程 ID : ${green}$pid${plain}"
        fi
    else
        echo -e "当前状态: ${red}未启动${plain}"
    fi
}

show_menu() {
    clear
    echo -e "
  ${green}V-UI 面板管理脚本${plain}
————————————————

  ${green}0.${plain} 退出脚本
————————————————
  ${green}1.${plain} 安装/更新 V-UI
  ${green}2.${plain} 卸载 V-UI
————————————————
  ${green}3.${plain} 启动 V-UI
  ${green}4.${plain} 停止 V-UI
  ${green}5.${plain} 重启 V-UI
  ${green}6.${plain} 查看 V-UI 状态
  ${green}7.${plain} 查看 V-UI 日志 (Ctrl+C 退出)
————————————————
 "
    check_status
    echo && read -p "请输入选择 [0-7]: " num

    case "${num}" in
        0) exit 0 ;;
        1) bash <(curl -Ls https://raw.githubusercontent.com/ForceMind/V-UI/refs/heads/master/install.sh) ;;
        2) 
            read -p "确定要卸载 V-UI 吗? [y/n]: " confirm
            if [[ "$confirm" == "y" ]]; then
                systemctl stop v-ui
                systemctl disable v-ui
                rm -rf /usr/local/v-ui
                rm /etc/systemd/system/v-ui.service
                rm /usr/bin/v-ui
                systemctl daemon-reload
                echo -e "${green}卸载完成${plain}"
            fi
            ;;
        3) systemctl start v-ui && echo -e "${green}V-UI 已启动${plain}" && sleep 1 && show_menu ;;
        4) systemctl stop v-ui && echo -e "${green}V-UI 已停止${plain}" && sleep 1 && show_menu ;;
        5) systemctl restart v-ui && echo -e "${green}V-UI 已重启${plain}" && sleep 1 && show_menu ;;
        6) systemctl status v-ui; read -p "按回车键返回..." ;;
        7) journalctl -u v-ui -f ;;
        *) echo -e "${red}请输入正确的数字 [0-7]${plain}" && sleep 1 && show_menu ;;
    esac
}

show_menu
