from cfg_generator.src.cfg_from_stdin import main as cfg_main


class CFGAnalyzer:
    def __init__(self, output_list):
        self.output_list = output_list
        self.paths = self.find_paths_from_output()

    def find_paths_from_output(self):
        all_paths = []

        for nodes in self.output_list:
            node_dict = {node['basic node id']: node for node in nodes}
            start_nodes = [node['basic node id'] for node in nodes if not node['previous node']]
            paths = []

            def is_loop_node(node_id):
                visited = set()
                stack = [node_id]
                while stack:
                    current = stack.pop()
                    if current in visited:
                        return True
                    visited.add(current)
                    stack.extend(node_dict[current]['next node'])
                return False

            def get_full_loop(node_id):
                loop_path = []
                visited = set()
                current = node_id
                while current not in visited:
                    visited.add(current)
                    loop_path.append(current)
                    next_nodes = node_dict[current]['next node']
                    if not next_nodes:
                        break
                    current = next_nodes[0]
                return loop_path

            def dfs(path, current_node, loop_count):
                node = node_dict[current_node]
                is_end_node = (node['end nodes'] == [None])

                if is_end_node:
                    if path and path[-1] != current_node:
                        path.append(current_node)
                    paths.append(path[:])

                    if is_loop_node(current_node):
                        full_loop = get_full_loop(current_node)
                        for repeat in range(1, 3):
                            looped_path = path[:]
                            for _ in range(repeat):
                                for loop_node in full_loop:
                                    if looped_path[-1] != loop_node:
                                        looped_path.append(loop_node)
                            if looped_path[-1] != current_node:
                                looped_path.append(current_node)
                            paths.append(looped_path)
                else:
                    for next_node in node['next node']:
                        if next_node in path:
                            loop_count[next_node] = loop_count.get(next_node, 0) + 1
                            if loop_count[next_node] > 2:
                                continue

                        dfs(path + [current_node], next_node, loop_count.copy())

            for start in start_nodes:
                node = node_dict[start]

                # ✅ بررسی حالت خاص: یک نود تنها که هم شروع و هم پایان است
                if not node['next node'] and node['end nodes']:
                    paths.append([start])  # مسیر تک نودی
                    continue  # فقط این مسیر رو داریم، dfs لازم نیست

                dfs([], start, {})

            all_paths.append(paths)

        return all_paths


class TestabilityCalculator:
    def __init__(self, paths, output_list):
        self.paths = paths
        self.output_list = output_list
        self.method_testability = self.calculate_node_testability_by_method()

    def calculate_node_testability_by_method(self):
        method_testability = {}

        for idx, path in enumerate(self.paths):
            total_paths = len(path)
            node_count = {}
            all_nodes = set()

            for p in path:
                unique_nodes_in_path = set(p)
                all_nodes.update(unique_nodes_in_path)
                for node in unique_nodes_in_path:
                    node_count[node] = node_count.get(node, 0) + 1

            method_name = None
            for nodes in self.output_list[idx]:
                method_name = nodes['function name']
                break

            if method_name not in method_testability:
                method_testability[method_name] = {}

            all_nodes_in_method = {node['basic node id'] for node in self.output_list[idx]}

            for node in all_nodes_in_method:
                testability_value = node_count.get(node, 0) / total_paths if total_paths > 0 else 0.0
                method_testability[method_name][node] = testability_value

        return method_testability

    def calculate_average_testability(self):
        average_testability = {}

        for method_name, node_testability in self.method_testability.items():
            total_score = sum(node_testability[node] for node in node_testability)
            num_nodes = len(node_testability)
            average_score = total_score / num_nodes if num_nodes > 0 else 0.0
            average_testability[method_name] = average_score

        return average_testability

    def calculate_weighted_average_testability(self):
        total_weighted_score = 0.0
        total_weight = 0.0

        for method_name, node_testability in self.method_testability.items():
            num_nodes = len(node_testability)
            if num_nodes == 0:
                continue

            weight = 1.0 / num_nodes
            average_score = sum(node_testability[node] for node in node_testability) / num_nodes

            total_weighted_score += average_score * weight
            total_weight += weight

        if total_weight > 0:
            weighted_average = total_weighted_score / total_weight
        else:
            weighted_average = 0.0

        return weighted_average


import os
import pandas as pd
from cfg_generator.src.cfg_from_stdin import main as cfg_main

import os
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from cfg_generator.src.cfg_from_stdin import main as cfg_main


def process_java_file(java_file):
    try:
        output_list = cfg_main(java_file)
        print("llllll")
        cfg_analyzer = CFGAnalyzer(output_list)
        paths = cfg_analyzer.paths
        testability_calculator = TestabilityCalculator(paths, output_list)

        weighted_avg = testability_calculator.calculate_weighted_average_testability()
        method_testability = testability_calculator.method_testability
        average_testability = testability_calculator.calculate_average_testability()

        return weighted_avg, method_testability, average_testability, paths, output_list

    except Exception as e:
        print(f"⚠️ خطا در فایل {java_file}: {e}")
        return None, None, None, None, None

import traceback  # بالا اضافه کن
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Manager
import traceback

def process_folder(idx, total, current_dir, java_files, output_csv_path, csv_lock):
    try:
        print(f"\n📁 ({idx}/{total}) در حال پردازش فولدر: {current_dir}")
        testabilities = []

        for java_file in java_files:
            print(f"\n🔹 فایل جاوا: {java_file}")
            weighted_avg, method_testability, avg_testability, paths, output_list = process_java_file(java_file)

            if paths is None or output_list is None:
                print("⚠️ پردازش این فایل با خطا مواجه شد. مسیرها موجود نیستند.")
                continue

            print(f"\n📌 مسیرهای CFG به ازای متدها:")
            for method_idx, method_paths in enumerate(paths):
                if method_idx < len(output_list) and output_list[method_idx]:
                    method_name = output_list[method_idx][0].get("function name", f"unnamed_method_{method_idx}")
                else:
                    method_name = f"unknown_method_{method_idx}"

                print(f"\n🔧 متد: {method_name}")
                for i, p in enumerate(method_paths, 1):
                    print(f"   Path {i}: {p}")

            if weighted_avg is None:
                continue

            print(f"   📈 تست‌پذیری میانگین وزنی فایل: {weighted_avg:.4f}")
            testabilities.append(weighted_avg)

        if testabilities:
            folder_avg = sum(testabilities) / len(testabilities)
            print(f"\n✅ میانگین تست‌پذیری فولدر '{current_dir}': {folder_avg:.4f}")

            # 🚧 استفاده از قفل برای جلوگیری از نوشتن همزمان در CSV
            with csv_lock:
                df_row = pd.DataFrame([{
                    "Folder": current_dir,
                    "Testability": round(folder_avg, 4)
                }])
                df_row.to_csv(output_csv_path, mode='a', header=not os.path.exists(output_csv_path), index=False, encoding="utf-8-sig")
        else:
            print(f"❌ هیچ فایل موفقی در فولدر '{current_dir}' پردازش نشد.")

    except Exception as e:
        print(f"\n💥 خطا در پردازش فولدر '{current_dir}': {e}")
        traceback.print_exc()

        # 📁 مرحله سوم: ثبت فولدر شکست‌خورده
        with csv_lock:
            with open("failed_folders.txt", "a", encoding="utf-8") as f:
                f.write(current_dir + "\n")

def main(root_path):
    output_csv_path = "folder_testability_summary.csv"

    if os.path.exists(output_csv_path):
        existing_df = pd.read_csv(output_csv_path)
        processed_folders = set(existing_df["Folder"].tolist())
    else:
        processed_folders = set()

    if os.path.isfile(root_path) and root_path.endswith(".java"):
        java_files_by_folder = {os.path.dirname(root_path): [root_path]}
    else:
        java_files_by_folder = {}
        for current_dir, _, files in os.walk(root_path):
            java_files = [os.path.join(current_dir, f) for f in files if f.endswith(".java")]
            if java_files:
                java_files_by_folder[current_dir] = java_files

    folders_to_process = [f for f in java_files_by_folder if f not in processed_folders]
    total = len(folders_to_process)

    print(f"\n🔄 تعداد کل فولدرهای در صف پردازش: {total}")

    with Manager() as manager:
        csv_lock = manager.Lock()

        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = []
            for idx, current_dir in enumerate(folders_to_process, 1):
                java_files = java_files_by_folder[current_dir]
                futures.append(executor.submit(
                    process_folder, idx, total, current_dir, java_files, output_csv_path, csv_lock
                ))

            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    print(f"⚠️ خطا در اجرای پردازش: {e}")

if __name__ == "__main__":
    file_path = "C:\\Users\\Lenovo\\Desktop\\cfg_test"
    main(file_path)
