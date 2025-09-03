import json
from collections import defaultdict, OrderedDict

from antlr4 import ParseTreeWalker, CommonTokenStream, FileStream

from cfg_generator.src.antlr.gen.JavaParser import JavaParser
from cfg_generator.src.cfg_from_stdin import main as main_cfg
from CDG import main as main_cdg, CDGExtractorVisitor
from BackwardSlicing import main as back_main
from cfg_generator.src.cfg_extractor.output import main as output_main
from cfg_generator.src.antlr.gen.JavaLexer import JavaLexer
from cfg_generator.src.antlr.gen.JavaParserListener import JavaParserListener


class ProgramSlicer:
    def track_blocks_in_file(self, file_path):
        input_stream = FileStream(file_path, encoding='utf-8')
        lexer = JavaLexer(input_stream)
        stream = CommonTokenStream(lexer)
        parser = JavaParser(stream)
        tree = parser.compilationUnit()

        # Generate the PDG using the visitor pattern
        pdg_visitor = CDGExtractorVisitor()
        pdg_visitor.visit(tree)

        # Track blocks for brace closures
        block_tracker = self.BlockTracker()
        walker = ParseTreeWalker()
        walker.walk(block_tracker, tree)

        return block_tracker.get_blocks()

    class BlockTracker(JavaParserListener):
        def __init__(self):
            self.blocks = []
            self.brace_stack = []

        def enterBlock(self, ctx):
            start_line = ctx.start.line
            self.brace_stack.append(start_line)

        def exitBlock(self, ctx):
            if self.brace_stack:
                start_line = self.brace_stack.pop()
                end_line = ctx.stop.line
                self.blocks.append({'start_line': start_line, 'end_line': end_line})

        def get_blocks(self):
            return self.blocks

    def find_reachable_blocks(self, cfg_nodes_list):
        reachable_blocks_list = []
        for cfg_nodes in cfg_nodes_list:
            graph = defaultdict(list)
            # Sort nodes by basic node id for deterministic processing
            sorted_nodes = sorted(cfg_nodes, key=lambda x: x['basic node id'])
            for node in sorted_nodes:
                current_id = node['basic node id']
                # Sort next nodes for deterministic processing
                sorted_next_nodes = sorted(node['next node'])
                for next_node in sorted_next_nodes:
                    graph[current_id].append(next_node)

            def dfs(node_id, path):
                reachable = set()
                stack = [(node_id, set(path))]
                while stack:
                    current, path = stack.pop()
                    if current in path:
                        continue
                    reachable.add(current)
                    path.add(current)
                    # Sort neighbors for deterministic processing
                    for neighbor in sorted(graph[current]):
                        if neighbor > current and neighbor not in path:
                            stack.append((neighbor, path.copy()))
                return reachable

            reachable_blocks = {}
            # Process nodes in sorted order
            for node in sorted_nodes:
                node_id = node['basic node id']
                next_nodes = node.get('next node', [])

                if not next_nodes:
                    reachable_blocks[node_id] = {node_id}
                else:
                    if any(next_node < node_id for next_node in next_nodes):
                        reachable_blocks[node_id] = {node_id}
                    else:
                        reachable_blocks[node_id] = dfs(node_id, set())

            reachable_blocks_list.append(reachable_blocks)
        return reachable_blocks_list

    def calculate_all_dominating_blocks_by_line(self, cdg_list):
        all_doms_list = []
        for cdg in cdg_list:
            # Sort CDG blocks by node id for deterministic processing
            sorted_cdg = sorted(cdg, key=lambda x: x['node id'])

            def calculate_dominating_blocks_by_line(line_number):
                # Use sorted iteration for deterministic behavior
                target_block = next((block for block in sorted_cdg if line_number in block.get('line', [])), None)
                if not target_block:
                    return []

                previous_nodes = target_block.get('previous node', [])
                if not previous_nodes:
                    return []

                # Sort previous nodes for deterministic processing
                sorted_prev_nodes = sorted(previous_nodes, key=lambda x: x['id'])
                prev_node_id = sorted_prev_nodes[0]['id']
                prev_node = next((block for block in sorted_cdg if block['node id'] == prev_node_id), None)

                if len(prev_node['next node']) == 1 and prev_node['next node'][0]['id'] == target_block['node id']:
                    return [target_block]

                def dfs(node_id, visited):
                    if node_id in visited:
                        return
                    visited.add(node_id)
                    current_block = next((block for block in sorted_cdg if block['node id'] == node_id), None)
                    if current_block:
                        # Sort next nodes for deterministic processing
                        sorted_next_nodes = sorted(current_block.get('next node', []), key=lambda x: x['id'])
                        for next_node in sorted_next_nodes:
                            dfs(next_node['id'], visited)

                visited_blocks = set()
                # Sort next nodes for deterministic processing
                sorted_next_nodes = sorted(prev_node.get('next node', []), key=lambda x: x['id'])
                for next_node in sorted_next_nodes:
                    dfs(next_node['id'], visited_blocks)

                visited_blocks.add(target_block['node id'])

                # Return sorted results
                return [block for block in sorted_cdg if block['node id'] in visited_blocks]

            all_doms = OrderedDict()
            # Process blocks in sorted order
            for block in sorted_cdg:
                for line in sorted(block.get('line', [])):
                    dominating_blocks = calculate_dominating_blocks_by_line(line)
                    # Sort dominating lines for deterministic output
                    all_doms[line] = sorted(set(line for block in dominating_blocks for line in block.get('line', [])))

            all_doms_list.append(all_doms)
            print("dommm", all_doms)
        return all_doms_list

    def map_lines_to_basic_blocks(self, cfg_list):
        line_to_basic_block_list = []
        basic_block_to_lines_list = []
        for cfg_nodes in cfg_list:
            line_to_basic_block = OrderedDict()
            basic_block_to_lines = OrderedDict()
            # Sort nodes for deterministic processing
            sorted_nodes = sorted(cfg_nodes, key=lambda x: x['basic node id'])
            for node in sorted_nodes:
                basic_block_id = node['basic node id']
                lines = node['line']
                for line in sorted(lines):
                    line_to_basic_block[int(line)] = basic_block_id
                basic_block_to_lines[basic_block_id] = set(map(int, sorted(lines)))
            line_to_basic_block_list.append(line_to_basic_block)
            basic_block_to_lines_list.append(basic_block_to_lines)
        return line_to_basic_block_list, basic_block_to_lines_list

    def calculate_reachable_blocks_by_line(self, reachable_blocks_list, line_to_basic_block_list,
                                           basic_block_to_lines_list):
        reachable_blocks_by_line_list = []
        for reachable_blocks, line_to_basic_block, basic_block_to_lines in zip(reachable_blocks_list,
                                                                               line_to_basic_block_list,
                                                                               basic_block_to_lines_list):
            reachable_blocks_by_line = OrderedDict()
            # Process lines in sorted order
            for line in sorted(line_to_basic_block.keys()):
                basic_block_id = line_to_basic_block[line]
                reachable_basic_blocks = reachable_blocks.get(basic_block_id, set())
                reachable_lines = set()
                for block_id in sorted(reachable_basic_blocks):
                    reachable_lines.update(basic_block_to_lines.get(block_id, set()))
                reachable_blocks_by_line[line] = sorted(reachable_lines)
            reachable_blocks_by_line_list.append(reachable_blocks_by_line)
        return reachable_blocks_by_line_list

    def format_reachable_blocks_by_line_output(self, reachable_blocks_by_line_list):
        formatted_output_list = []
        for reachable_blocks_by_line in reachable_blocks_by_line_list:
            formatted_output = []
            # Process lines in sorted order
            for line in sorted(reachable_blocks_by_line.keys()):
                reachable_lines = reachable_blocks_by_line[line]
                formatted_output.append(f"R(L{line}): {reachable_lines}")
            formatted_output_list.append(formatted_output)
        return formatted_output_list

    def convert_dominating_blocks_to_basic_blocks(self, dominating_blocks_by_line_list, line_to_basic_block_list):
        dominating_blocks_by_basic_block_list = []
        for dominating_blocks_by_line, line_to_basic_block in zip(dominating_blocks_by_line_list,
                                                                  line_to_basic_block_list):
            dominating_blocks_by_basic_block = OrderedDict()
            # Process lines in sorted order
            for line in sorted(dominating_blocks_by_line.keys()):
                dominating_lines = dominating_blocks_by_line[line]
                basic_block_ids = [block for l, block in line_to_basic_block.items() if l == line]
                if not basic_block_ids:
                    print(f"⚠️ Warning: No basic block found for line {line}")
                    continue
                dominating_basic_blocks = set()
                for dominating_line in sorted(dominating_lines):
                    blocks = [block for l, block in line_to_basic_block.items() if l == dominating_line]
                    if not blocks:
                        print(f"⚠️ Warning: No basic block found for dominating line {dominating_line}")
                    else:
                        dominating_basic_blocks.update(blocks)
                for block_id in sorted(basic_block_ids):
                    dominating_blocks_by_basic_block[block_id] = sorted(dominating_basic_blocks)
            dominating_blocks_by_basic_block_list.append(dominating_blocks_by_basic_block)
        return dominating_blocks_by_basic_block_list

    def format_dominating_blocks_output(self, dominating_blocks_by_basic_block_list):
        formatted_output_list = []
        for dominating_blocks_by_basic_block in dominating_blocks_by_basic_block_list:
            formatted_output = []
            # Process basic blocks in sorted order
            for basic_block_id in sorted(dominating_blocks_by_basic_block.keys()):
                dominating_basic_blocks = dominating_blocks_by_basic_block[basic_block_id]
                formatted_output.append(f"D(B{basic_block_id}): {dominating_basic_blocks}")
            formatted_output_list.append(formatted_output)
        return formatted_output_list

    def format_dominating_blocks_by_line_output(self, dominating_blocks_by_line_list):
        formatted_output_list = []
        for dominating_blocks_by_line in dominating_blocks_by_line_list:
            formatted_output = []
            # Process lines in sorted order
            for line in sorted(dominating_blocks_by_line.keys()):
                dominating_lines = dominating_blocks_by_line[line]
                formatted_output.append(f"D(L{line}): {dominating_lines}")
            formatted_output_list.append(formatted_output)
        return formatted_output_list

    def calculate_boundary_blocks(self, cfg_list, reachable_blocks_list, dominating_blocks_list):
        boundary_blocks_list = []
        for cfg_nodes, reachable_blocks, dominating_blocks in zip(cfg_list, reachable_blocks_list,
                                                                  dominating_blocks_list):
            intersection_results = OrderedDict()
            # Process blocks in sorted order
            for block_id in sorted(reachable_blocks.keys()):
                if block_id in dominating_blocks:
                    intersection_results[block_id] = sorted(
                        reachable_blocks[block_id] & set(dominating_blocks.get(block_id, set())))

            line_to_basic_node = OrderedDict()
            # Process nodes in sorted order
            sorted_nodes = sorted(cfg_nodes, key=lambda x: x['basic node id'])
            for node in sorted_nodes:
                for line in sorted(node['line']):
                    line_to_basic_node[int(line)] = node['basic node id']

            line_boundary_blocks = OrderedDict()
            # Process lines in sorted order
            for line in sorted(line_to_basic_node.keys()):
                line_boundary_blocks[line] = []
                basic_node_id = line_to_basic_node[line]
                # Process intersection results in sorted order
                for block_id in sorted(intersection_results.keys()):
                    if basic_node_id in intersection_results[block_id]:
                        line_boundary_blocks[line].append(block_id)

            boundary_blocks_list.append(line_boundary_blocks)
        return boundary_blocks_list

    def calculate_reachables_for_boundary_blocks(self, boundary_blocks_list, reachable_blocks_list):
        line_reachable_blocks_list = []
        for boundary_blocks, reachable_blocks in zip(boundary_blocks_list, reachable_blocks_list):
            line_reachable_blocks = OrderedDict()
            # Process lines in sorted order
            for line in sorted(boundary_blocks.keys()):
                boundary_blocks_list_for_line = boundary_blocks[line]
                line_reachable_blocks[line] = OrderedDict()
                # Process boundary blocks in sorted order
                for block in sorted(boundary_blocks_list_for_line):
                    line_reachable_blocks[line][block] = sorted(reachable_blocks.get(block, set()))
            line_reachable_blocks_list.append(line_reachable_blocks)
        return line_reachable_blocks_list

    def get_lines_for_reachable_blocks(self, cfg_list, reachable_blocks_for_boundary_list):
        lines_for_reachable_blocks_list = []
        for cfg_nodes, reachable_blocks_for_boundary in zip(cfg_list, reachable_blocks_for_boundary_list):
            block_to_lines = OrderedDict()
            # Process nodes in sorted order
            sorted_nodes = sorted(cfg_nodes, key=lambda x: x['basic node id'])
            for node in sorted_nodes:
                block_to_lines[node['basic node id']] = set(map(int, sorted(node['line'])))

            lines_for_reachable_blocks = OrderedDict()
            # Process lines in sorted order
            for line in sorted(reachable_blocks_for_boundary.keys()):
                lines_for_reachable_blocks[line] = OrderedDict()
                reachable_blocks = reachable_blocks_for_boundary[line]
                # Process blocks in sorted order
                for block in sorted(reachable_blocks.keys()):
                    reachable_block_ids = reachable_blocks[block]
                    lines_set = set()
                    for block_id in sorted(reachable_block_ids):
                        lines_set.update(block_to_lines.get(block_id, []))
                    lines_for_reachable_blocks[line][block] = sorted(lines_set)
            lines_for_reachable_blocks_list.append(lines_for_reachable_blocks)
        return lines_for_reachable_blocks_list

    def compute_common_slicing(self, dict_1, dict_2):
        """
        Calculates the intersection between slicing lines and block lines for all variables.
        """
        # Flatten dict_2 with deterministic ordering
        dict_2_flat = OrderedDict()
        # Sort the list of dictionaries by their string representation for deterministic order
        for item in sorted(dict_2, key=lambda x: str(sorted(x.items()))):
            # Sort each dictionary's items for deterministic update
            sorted_item = OrderedDict(sorted(item.items()))
            dict_2_flat.update(sorted_item)

        result = OrderedDict()

        # Iterate in sorted key order for determinism
        for key in sorted(dict_1.keys()):
            if key not in dict_2_flat:
                continue

            slicing_aggregate = set()
            duplicate_aggregate = set()

            # Deterministic aggregation: iterate over variables in sorted order
            for var_name in sorted(dict_1[key].keys()):
                var_data = dict_1[key][var_name]

                main_slicing = var_data.get("main_slicing", [])
                if main_slicing:
                    # Handle list of lists vs flat list
                    if any(isinstance(x, (list, tuple, set)) for x in main_slicing):
                        for seq in main_slicing:
                            slicing_aggregate.update(seq)
                    else:
                        slicing_aggregate.update(main_slicing)

                duplicate_aggregate.update(var_data.get("duplicate_lines", []))

            if not slicing_aggregate and not duplicate_aggregate:
                continue

            matched_blocks = []
            # Sort blocks to avoid PYTHONHASHSEED influence
            for block, lines in sorted(dict_2_flat[key].items()):
                lines_set = set(lines)
                common_slicing = slicing_aggregate & lines_set
                common_duplicates = duplicate_aggregate & lines_set

                if common_slicing or common_duplicates:
                    matched_blocks.append({
                        f"B{block}": {
                            "main_slicing": sorted(common_slicing),
                            "duplicate_lines": sorted(common_duplicates)
                        }
                    })

            # Sort matched_blocks by block label for stability
            if matched_blocks:
                result[key] = sorted(matched_blocks, key=lambda x: list(x.keys())[0])

        return result

    def adjust_slicing_with_braces(self, slicing_data, blocks):
        """
        Checks if a line with an opening brace is present in main_slicing or duplicate_lines,
        and if so, also adds the corresponding closing brace.
        """
        # Create a dictionary to quickly find the closing brace from the opening brace
        brace_pairs = {block['start_line']: block['end_line'] for block in blocks}

        for key in sorted(slicing_data.keys()):
            block_list = slicing_data[key]
            for block_data in block_list:
                for block_id, values in block_data.items():
                    # Process main_slicing
                    updated_main_slicing = set(values['main_slicing'])
                    for line in sorted(values['main_slicing']):
                        if line in brace_pairs:
                            updated_main_slicing.add(brace_pairs[line])
                    values['main_slicing'] = sorted(updated_main_slicing)

                    # Process duplicate_lines
                    updated_duplicate_lines = set(values['duplicate_lines'])
                    for line in sorted(values['duplicate_lines']):
                        if line in brace_pairs:
                            updated_duplicate_lines.add(brace_pairs[line])
                    values['duplicate_lines'] = sorted(updated_duplicate_lines)

        return slicing_data

    def main(self, file_path):
        # دیکشنری برای ذخیره نتایج همه متدها
        results = OrderedDict()

        # گرفتن CFG و CDG همه متدها
        cfg_list = main_cfg(file_path)
        visitor = CDGExtractorVisitor()
        cdg_list = main_cdg(file_path, visitor)

        # پیمایش روی هر متد (CFG و CDG به صورت جفتی)
        for func_index, (cfg_nodes, cdg_nodes) in enumerate(zip(cfg_list, cdg_list)):
            func_name = cfg_nodes[0].get("function name", f"Function_{func_index}")
            print("=" * 100)
            print(f"🔹 Analyzing function: {func_name}")
            print("=" * 100)

            # بقیه‌ی کد بدون تغییر
            print("Calculating Reachable Blocks:")
            reachable_blocks_list = self.find_reachable_blocks([cfg_nodes])
            reachable_blocks_list_formatted = []
            for reachable_blocks in reachable_blocks_list:
                reachable_blocks_list_formatted.append(
                    [f"R(B{block}): {sorted(reachable)}" for block, reachable in sorted(reachable_blocks.items())]
                )
            for formatted_list in reachable_blocks_list_formatted:
                formatted_list.sort()
                print("\n".join(formatted_list))

            line_to_basic_block_list, basic_block_to_lines_list = self.map_lines_to_basic_blocks([cfg_nodes])
            reachable_blocks_by_line_list = self.calculate_reachable_blocks_by_line(
                reachable_blocks_list, line_to_basic_block_list, basic_block_to_lines_list
            )
            formatted_reachable_blocks_by_line = self.format_reachable_blocks_by_line_output(
                reachable_blocks_by_line_list
            )
            print("\nFormatted Reachable Blocks by Line:")
            for formatted_output in formatted_reachable_blocks_by_line:
                print("\n".join(formatted_output))

            print("\nCalculating Dominating Blocks:")
            all_dominating_blocks_by_line_list = self.calculate_all_dominating_blocks_by_line([cdg_nodes])
            dominating_blocks_by_basic_block_list = self.convert_dominating_blocks_to_basic_blocks(
                all_dominating_blocks_by_line_list, line_to_basic_block_list
            )
            formatted_dominating_blocks = self.format_dominating_blocks_output(
                dominating_blocks_by_basic_block_list
            )
            print("\nFormatted Dominating Blocks by Basic Block:")
            for formatted_output in formatted_dominating_blocks:
                print("\n".join(formatted_output))

            formatted_dominating_blocks_by_line = self.format_dominating_blocks_by_line_output(
                all_dominating_blocks_by_line_list
            )
            print("\nFormatted Dominating Blocks by Line:")
            for formatted_output in formatted_dominating_blocks_by_line:
                print("\n".join(formatted_output))

            boundary_blocks_list = self.calculate_boundary_blocks(
                [cfg_nodes], reachable_blocks_list, dominating_blocks_by_basic_block_list
            )
            print("\nComputed Boundary Blocks:")
            for boundary_blocks in boundary_blocks_list:
                for line in sorted(boundary_blocks.keys()):
                    print(f"Boundary Blocks for Line {line}: {boundary_blocks[line]}")

            reachable_blocks_for_boundary_list = self.calculate_reachables_for_boundary_blocks(
                boundary_blocks_list, reachable_blocks_list
            )
            print("\nReachable Blocks for Each Boundary Block:")
            for reachable_blocks_for_boundary in reachable_blocks_for_boundary_list:
                for line in sorted(reachable_blocks_for_boundary.keys()):
                    print(f"Line {line}:")
                    for block, reachables in sorted(reachable_blocks_for_boundary[line].items()):
                        print(f"  B{block} → Reachable: {sorted(reachables)}")

            lines_for_reachable_blocks_list = self.get_lines_for_reachable_blocks(
                [cfg_nodes], reachable_blocks_for_boundary_list
            )
            print("lines_for_reachable_blocks_list lines_for_reachable_blocks_list", lines_for_reachable_blocks_list)
            print("\nReachable Blocks for Each Line with Corresponding Code Lines:")
            for lines_for_reachable_blocks in lines_for_reachable_blocks_list:
                for line in sorted(lines_for_reachable_blocks.keys()):
                    print(f"Line {line}:")
                    for block, lines in sorted(lines_for_reachable_blocks[line].items()):
                        print(f"  B{block} → Reachable Lines: {sorted(lines)}")

            filtered_used_variables_dict = output_main(file_path)
            slicing_data = back_main(file_path, filtered_used_variables_dict)
            output = self.compute_common_slicing(slicing_data, lines_for_reachable_blocks_list)

            print("Tracking Block Lines:")
            blocks = self.track_blocks_in_file(file_path)
            for block in sorted(blocks, key=lambda x: x['start_line']):
                print(f"Block starts at line {block['start_line']}, ends at line {block['end_line']}")

            adjusted_output = self.adjust_slicing_with_braces(output, blocks)

            formatted_output = OrderedDict()
            for key in sorted(adjusted_output.keys()):
                formatted_output[key] = [
                    {sub_key: {
                        "main_slicing": sub_value["main_slicing"],
                        "duplicate_lines": sub_value["duplicate_lines"]
                    } for sub_key, sub_value in sorted(item.items())}
                    for item in adjusted_output[key]
                ]

            results[func_name] = formatted_output

            for key in sorted(adjusted_output.keys()):
                print(f"Key: {key}")
                for item in adjusted_output[key]:
                    for sub_key, sub_value in sorted(item.items()):
                        print(f"  {sub_key}:")
                        print(f"    main_slicing: {sub_value['main_slicing']}")
                        print(f"    duplicate_lines: {sub_value['duplicate_lines']}")

        return results


if __name__ == "__main__":
    file_path = "test\\f.java"
    slicer = ProgramSlicer()
    results = slicer.main(file_path)
    print("\nFinal Results Dictionary:")
    print(json.dumps(results, indent=2))