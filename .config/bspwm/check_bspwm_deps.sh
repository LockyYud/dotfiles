#!/bin/bash

echo "=== BSPWM DEPENDENCY CHECK ==="
echo "Checking all required packages and folders..."

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

missing_packages=()
missing_folders=()
missing_files=()

# Required packages from your bspwmrc
packages=(
    "bspwm"          # Window manager
    "sxhkd"          # Hotkey daemon  
    "polybar"        # Status bar
    "picom"          # Compositor
    "feh"            # Wallpaper setter
    "rofi"           # App launcher (likely in sxhkdrc)
    "kitty"          # Terminal (likely in sxhkdrc)
)

echo -e "\n${YELLOW}=== CHECKING PACKAGES ===${NC}"
for package in "${packages[@]}"; do
    if command -v "$package" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} $package is installed"
    else
        echo -e "${RED}✗${NC} $package is NOT installed"
        missing_packages+=("$package")
    fi
done

# Check required folders
echo -e "\n${YELLOW}=== CHECKING FOLDERS ===${NC}"
folders=(
    "$HOME/.config/bspwm"
    "$HOME/.config/sxhkd" 
    "$HOME/.config/polybar"
    "$HOME/.config/picom"
    "$HOME/.config/rofi"
    "$HOME/Pictures/wallpapers"
)

for folder in "${folders[@]}"; do
    if [ -d "$folder" ]; then
        echo -e "${GREEN}✓${NC} $folder exists"
    else
        echo -e "${RED}✗${NC} $folder does NOT exist"
        missing_folders+=("$folder")
    fi
done

# Check required config files
echo -e "\n${YELLOW}=== CHECKING CONFIG FILES ===${NC}"
config_files=(
    "$HOME/.config/bspwm/bspwmrc"
    "$HOME/.config/sxhkd/sxhkdrc"
    "$HOME/.config/polybar/launch.sh"
)

for file in "${config_files[@]}"; do
    if [ -f "$file" ]; then
        if [ -x "$file" ]; then
            echo -e "${GREEN}✓${NC} $file exists and is executable"
        else
            echo -e "${YELLOW}⚠${NC} $file exists but is NOT executable"
        fi
    else
        echo -e "${RED}✗${NC} $file does NOT exist"
        missing_files+=("$file")
    fi
done

# Check wallpaper files mentioned in bspwmrc
echo -e "\n${YELLOW}=== CHECKING WALLPAPER FILES ===${NC}"
wallpapers=(
    "$HOME/Pictures/wallpapers/natra3.jpg"
    "$HOME/Pictures/wallpapers/natra2.jpg"
)

for wallpaper in "${wallpapers[@]}"; do
    if [ -f "$wallpaper" ]; then
        echo -e "${GREEN}✓${NC} $wallpaper exists"
    else
        echo -e "${RED}✗${NC} $wallpaper does NOT exist"
        missing_files+=("$wallpaper")
    fi
done

# Check optional but commonly needed packages
echo -e "\n${YELLOW}=== CHECKING OPTIONAL PACKAGES ===${NC}"
optional_packages=(
    "dunst"          # Notification daemon
    "xsetroot"       # X root window parameter setting utility
    "wmctrl"         # Window manager control
)

for package in "${optional_packages[@]}"; do
    if command -v "$package" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} $package is installed (optional)"
    else
        echo -e "${YELLOW}⚠${NC} $package is NOT installed (optional but recommended)"
    fi
done

# Summary and installation commands
echo -e "\n${YELLOW}=== SUMMARY ===${NC}"

if [ ${#missing_packages[@]} -eq 0 ] && [ ${#missing_folders[@]} -eq 0 ] && [ ${#missing_files[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ All required dependencies are satisfied!${NC}"
else
    echo -e "${RED}✗ Some dependencies are missing:${NC}"
    
    if [ ${#missing_packages[@]} -gt 0 ]; then
        echo -e "\n${YELLOW}Missing packages:${NC}"
        for package in "${missing_packages[@]}"; do
            echo "  - $package"
        done
        
        echo -e "\n${YELLOW}To install missing packages:${NC}"
        echo "sudo apt update"
        echo "sudo apt install ${missing_packages[*]}"
    fi
    
    if [ ${#missing_folders[@]} -gt 0 ]; then
        echo -e "\n${YELLOW}Missing folders (will be created):${NC}"
        for folder in "${missing_folders[@]}"; do
            echo "  - $folder"
            mkdir -p "$folder"
            echo -e "${GREEN}✓${NC} Created $folder"
        done
    fi
    
    if [ ${#missing_files[@]} -gt 0 ]; then
        echo -e "\n${YELLOW}Missing files:${NC}"
        for file in "${missing_files[@]}"; do
            echo "  - $file"
        done
    fi
fi

# Check syntax of bspwmrc
echo -e "\n${YELLOW}=== CHECKING BSPWMRC SYNTAX ===${NC}"
if [ -f "$HOME/.config/bspwm/bspwmrc" ]; then
    if bash -n "$HOME/.config/bspwm/bspwmrc" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} bspwmrc syntax is valid"
    else
        echo -e "${RED}✗${NC} bspwmrc has syntax errors"
        echo "Run: bash -n ~/.config/bspwm/bspwmrc"
    fi
fi

echo -e "\n${YELLOW}=== NEXT STEPS ===${NC}"
echo "1. Install missing packages (if any)"
echo "2. Create missing config files (sxhkdrc, polybar config, etc.)"
echo "3. Add wallpaper images to ~/Pictures/wallpapers/"
echo "4. Test with: Xephyr -screen 1024x768 :1 & DISPLAY=:1 bspwm"