import os
import shutil
from collections import defaultdict

def find_duplicate_songs(folder_path):
    print(f"Đang quét thư mục: {folder_path} ...\n")
    
    # Gom nhóm file theo 7 ký tự đầu tiên của tên file
    name_dict = defaultdict(list)
    for root, _, files in os.walk(folder_path):
        for filename in files:
            if filename.lower().endswith('.mp3'):
                filepath = os.path.join(root, filename)
                
                # Lấy tên file không bao gồm phần mở rộng (.mp3)
                base_name = os.path.splitext(filename)[0]
                
                # Lấy 7 ký tự đầu (chuyển về chữ thường để so sánh không phân biệt hoa/thường)
                prefix = base_name[:15].lower()
                name_dict[prefix].append(filepath)

    # Lọc ra các nhóm có từ 2 file trở lên (có khả năng trùng)
    duplicates = {prefix: paths for prefix, paths in name_dict.items() if len(paths) > 1}

    if not duplicates:
        print("Tuyệt vời! Không tìm thấy bài hát trùng lặp nào dựa trên 7 ký tự đầu của tên.")
        return

    # Thư mục dùng để chứa các file bị trùng lặp (các file thừa)
    duplicate_folder = os.path.join(folder_path, "Duplicated_Songs")
    if not os.path.exists(duplicate_folder):
        os.makedirs(duplicate_folder)

    print(f"Tìm thấy {len(duplicates)} nhóm bài hát có thể bị trùng. Đang tiến hành di chuyển...\n")
    for i, (prefix, paths) in enumerate(duplicates.items(), 1):
        print(f"--- Nhóm trùng lặp thứ {i} (Tiền tố: '{prefix}') ---")
        
        # Gom kèm theo dung lượng file để so sánh
        files_with_size = []
        for path in paths:
            try:
                files_with_size.append((os.path.getsize(path), path))
            except OSError:
                pass
        
        # Sắp xếp danh sách file theo dung lượng giảm dần (file lớn nhất đứng đầu)
        files_with_size.sort(key=lambda x: x[0], reverse=True)
        
        # Di chuyển TẤT CẢ các file trong nhóm trùng lặp vào thư mục Duplicated_Songs
        for size, path in files_with_size:
            try:
                filename = os.path.basename(path)
                dest_path = os.path.join(duplicate_folder, filename)
                
                # Nếu có trùng tên file trong thư mục Duplicated_Songs thì thêm hậu tố để tránh ghi đè
                counter = 1
                while os.path.exists(dest_path):
                    name, ext = os.path.splitext(filename)
                    dest_path = os.path.join(duplicate_folder, f"{name}_{counter}{ext}")
                    counter += 1
                    
                shutil.move(path, dest_path)
                print(f"  [ĐÃ DI CHUYỂN] {path} -> {dest_path}")
            except Exception as e:
                print(f"  [LỖI DI CHUYỂN] {path}: {e}")
        print()

if __name__ == "__main__":
    # Thư mục chứa nhạc cần quét
    MUSIC_FOLDER = r"g:\Users\ACER\Music"
    find_duplicate_songs(MUSIC_FOLDER)