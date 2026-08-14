#!/bin/bash

# Đọc danh sách key từ file
file="$HOME/.config/secrets/keys.txt"

# Lấy danh sách tên key (không hiển thị giá trị)
key_name=$(cut -d '=' -f1 "$file" | rofi -dmenu -i -p "Chọn key:" -theme ~/.config/rofi/themes/get_key.rasi)

# Nếu có key_name, lấy giá trị key
if [[ -n "$key_name" ]]; then
    key_value=$(grep "^$key_name=" "$file" | cut -d '=' -f2-)
    
    # Copy key vào clipboard
    echo -n "$key_value" | xclip -selection clipboard
    
    # Hiển thị thông báo
    notify-send "Đã copy key!" "Key: $key_name"
fi

