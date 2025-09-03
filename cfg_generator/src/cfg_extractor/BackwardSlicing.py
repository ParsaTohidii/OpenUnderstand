from typing import List, Set, Dict
from antlr4 import FileStream, CommonTokenStream, ParseTreeWalker
from cfg_generator.src.antlr.gen.JavaLexer import JavaLexer
from cfg_generator.src.antlr.gen.JavaParser import JavaParser
from cfg_generator.src.antlr.gen.JavaParserListener import JavaParserListener
from cfg_generator.src.cfg_extractor.pdg import PDGExtractorVisitor
from cfg_generator.src.cfg_extractor.output import main as main_output


class BlockTracker(JavaParserListener):
    def __init__(self):
        self.blocks = []  # To store block start and end lines
        self.brace_stack = []  # Stack to track open braces and their lines

    def enterBlock(self, ctx):
        """
        Called when a block (e.g., { ... }) is entered.
        """
        start_line = ctx.start.line
        self.brace_stack.append(start_line)

    def exitBlock(self, ctx):
        """
        Called when a block (e.g., { ... }) is exited.
        """
        start_line = self.brace_stack.pop()
        end_line = ctx.stop.line
        self.blocks.append({'start_line': start_line, 'end_line': end_line})

    def get_blocks(self):
        return self.blocks


class BackwardSlicing:
    def __init__(self, pdg_nodes: Dict[int, Dict], method_nodes: Dict[int, Dict]):
        self.pdg_nodes = pdg_nodes
        self.method_nodes = method_nodes

    def get_variable_dependencies(self, line: int, variable: str) -> Set[int]:
        visited_nodes = set()
        variable = variable.strip()  # Normalize the variable name

        # Identify the method containing the given line
        method_name = None
        for node in self.pdg_nodes.values():
            if line in node['line']:
                method_name = node['function name']
                break
        if method_name is None:
            raise ValueError(f"No method found containing line {line}.")

        # Get the range of lines in the identified method
        method_lines = {
            node_id: node for node_id, node in self.pdg_nodes.items()
            if node['function name'] == method_name
        }

        # Initialize nodes to visit based on the variable and its occurrence at the specified line
        start_node_id = None
        for node_id, node_info in method_lines.items():
            if line in node_info['line'] and variable in [v.strip() for v in node_info.get('use', [])]:
                start_node_id = node_id
                break

        # If the variable is defined at the specified line, include it as a starting point
        for node_id, node_info in method_lines.items():
            if line in node_info['line'] and variable in [v.strip() for v in node_info.get('def', [])]:
                start_node_id = node_id
                break

        if start_node_id is None:
            # Special case: if the line only defines the variable with no further dependencies
            for node_id, node_info in method_lines.items():
                if line in node_info['line'] and variable in [v.strip() for v in node_info.get('def', [])]:
                    return {node_id}
            raise ValueError(f"No node found at line {line} using or defining the variable '{variable}'.")

        # Add the relevant previous nodes for the start node
        initial_nodes_to_visit = set()
        start_node_info = self.pdg_nodes[start_node_id]
        for prev_node_info in start_node_info.get('previous node', []):
            if prev_node_info.get('type') == 'data' and prev_node_info.get('variable').strip() == variable:
                initial_nodes_to_visit.add(prev_node_info['id'])
            elif prev_node_info.get('type') == 'control':
                # Include control dependencies as well
                initial_nodes_to_visit.add(prev_node_info['id'])

        # Begin backward slicing from the identified previous nodes
        nodes_to_visit = initial_nodes_to_visit.copy()
        while nodes_to_visit:
            current_node_id = nodes_to_visit.pop()
            if current_node_id in visited_nodes:
                continue

            visited_nodes.add(current_node_id)
            current_node = self.pdg_nodes[current_node_id]

            # Follow data dependencies within the same method
            for prev_node_info in current_node.get('previous node', []):
                prev_node_id = prev_node_info['id']
                if prev_node_id not in visited_nodes and prev_node_id in method_lines:
                    prev_node_line = method_lines[prev_node_id]['line']
                    if any(line >= l for l in prev_node_line):
                        nodes_to_visit.add(prev_node_id)

            # Follow variable definitions within the same method, but ensure it's related to the variable of interest
            for dependent_variable in current_node.get('use', []):
                for dependent_node_id, dependent_node in method_lines.items():
                    if dependent_variable.strip() == variable:
                        for def_var in dependent_node.get('def', []):
                            if def_var.strip() == variable and dependent_node_id not in visited_nodes:
                                dependent_node_line = dependent_node['line']
                                if any(line >= l for l in
                                       dependent_node_line):
                                    nodes_to_visit.add(dependent_node_id)

            # Handle control dependencies, which are relevant
            for prev_node_info in current_node.get('previous node', []):
                if prev_node_info.get('type') == "control":
                    control_node_id = prev_node_info['id']
                    if control_node_id not in visited_nodes and control_node_id in method_lines:
                        control_node_line = method_lines[control_node_id]['line']
                        if any(line >= l for l in control_node_line):
                            nodes_to_visit.add(control_node_id)
            # Ensure we add relevant definitions for the variable
            for node_id, node_info in method_lines.items():
                if any(variable.strip() == v.strip() for v in node_info.get('def', [])):
                    if node_id not in visited_nodes and any(line >= l for l in node_info['line']):
                        nodes_to_visit.add(node_id)

        return visited_nodes

    def slice(self, line: int, variable: str) -> List[str]:
        dependent_nodes = self.get_variable_dependencies(line, variable)

        # Add the initial node that uses or defines the variable on the specified line
        start_node_id = None
        for node_id, node_info in self.pdg_nodes.items():
            if line in node_info['line'] and (
                    variable in [v.strip() for v in node_info.get('use', [])] or
                    variable in [v.strip() for v in node_info.get('def', [])]
            ):
                start_node_id = node_id
                break

        if start_node_id is not None:
            dependent_nodes.add(start_node_id)

        # Include the specified line in the output even if it wasn't explicitly added
        for node_id, node_info in self.pdg_nodes.items():
            if line in node_info['line']:
                dependent_nodes.add(node_id)

        # Filter out lines that include method declarations
        def is_method_declaration(text: str) -> bool:
            return "(" in text and ")" in text and "{" in text and (
                    "public " in text or "private " in text or "protected " in text or "void " in text)

        # Create a sorted list of code lines for all dependent nodes
        dependent_code_lines = [
            (self.pdg_nodes[node_id]['line'][0], self.pdg_nodes[node_id]['text'])
            for node_id in sorted(dependent_nodes, key=lambda x: self.pdg_nodes[x]['line'][0])
            if not is_method_declaration(self.pdg_nodes[node_id]['text'])  # Exclude method declarations
        ]

        return dependent_code_lines

    def affects_variable(self, node_id: int, variable: str, method_lines: Dict[int, Dict]) -> bool:
        """
        Check if a given node affects the specified variable. This can be a control dependency
        or a data dependency.
        """
        node_info = self.pdg_nodes[node_id]
        # Check if the node defines the variable or uses it in a way that would affect its value
        if variable in node_info.get('use', []):
            return True
        if variable in node_info.get('def', []):
            return True
        return False


def analyze_unrelated_lines(unrelated_lines, pdg_nodes, slicing_tool, other_slicing):
    """
    Analyze unrelated lines: For each line, identify defined and used variables
    and perform backward slicing. Display the results for defined and used variables.
    """
    for line_num in unrelated_lines:
        for node_id, node in pdg_nodes.items():
            if line_num in node['line']:
                # Identify defined and used variables
                defined_vars = node.get('def', [])
                used_vars = node.get('use', [])

                # If no variables are defined or used, display a message
                if not defined_vars and not used_vars:
                    print(f"\nNo variables defined or used at line {line_num}.")
                    continue

                # Perform backward slicing for defined variables
                for variable in defined_vars:
                    print(f"\nBackward slice for defined variable '{variable}' at line {line_num}:")
                    try:
                        dependent_lines = slicing_tool.slice(line_num, variable)
                        print(dependent_lines)

                        # Extract line numbers from the output and add to `other_slicing`
                        result_line_numbers = {line[0] for line in dependent_lines}

                        # Add the line numbers from the result to the `other_slicing`
                        other_slicing.append(sorted(result_line_numbers))

                    except ValueError as e:
                        print(f"Error while slicing defined variable '{variable}': {e}")

                # Perform backward slicing for used variables
                for variable in used_vars:
                    print(f"\nBackward slice for used variable '{variable}' at line {line_num}:")
                    try:
                        dependent_lines = slicing_tool.slice(line_num, variable)
                        print(dependent_lines)

                        # Extract line numbers from the output and add to `other_slicing`
                        result_line_numbers = {line[0] for line in dependent_lines}

                        # Add the line numbers from the result to the `other_slicing`
                        other_slicing.append(sorted(result_line_numbers))

                    except ValueError as e:
                        print(f"Error while slicing used variable '{variable}': {e}")


def main(input_file_path: str, filtered_used_variables_dict: dict):
    """
    Input: Source code file path and dictionary containing lines and variables.
    Perform backward slicing for each line and variable pair and return results.
    """
    # Parse the input Java file
    input_stream = FileStream(input_file_path, encoding='utf-8')
    lexer = JavaLexer(input_stream)
    stream = CommonTokenStream(lexer)
    parser = JavaParser(stream)
    tree = parser.compilationUnit()

    # Generate the PDG using the visitor pattern
    pdg_visitor = PDGExtractorVisitor()
    pdg_visitor.visit(tree)

    # Track blocks for brace closures
    block_tracker = BlockTracker()
    walker = ParseTreeWalker()
    walker.walk(block_tracker, tree)

    # Create BackwardSlicing instance
    slicing_tool = BackwardSlicing(pdg_visitor.pdg_nodes, pdg_visitor.method_nodes)

    # Initialize results dictionary
    results = {}

    # Check if there are at least two variables in the dictionary
    total_variables = sum(len(variables) for variables in filtered_used_variables_dict.values())
    if total_variables < 2:
        print("\nLess than two variables found in the dictionary. Results will not be saved.")
    else:
        print("\nAt least two variables found. Results will be saved.")

    # Perform backward slicing for each line and variable pair
    for line, variables in filtered_used_variables_dict.items():
        line = int(line)
        results[line] = {}
        for variable in variables:
            # Initialize results lists for each variable
            main_slicing = []
            other_slicing = []

            # Perform backward slicing for the main variable
            print(f"\nBackward slice for variable '{variable}' at line {line}:")
            try:
                dependent_lines = slicing_tool.slice(line, variable)
                print("Dependent lines:", dependent_lines)

                all_lines = {line[0] for line in dependent_lines}

                main_slicing.append(sorted(all_lines))
                print("Main Slicing:", main_slicing)
            except ValueError as e:
                print(f"Error: {e}")

            # Identify lines in the method not included in the slice
            method_name = None
            for node in slicing_tool.pdg_nodes.values():
                if line in node['line']:
                    method_name = node['function name']
                    break

            if not method_name:
                raise ValueError(f"No method found containing line {line}.")

            # Get all lines in the method
            method_lines = {
                node_id: node for node_id, node in slicing_tool.pdg_nodes.items()
                if node['function name'] == method_name
            }

            all_method_lines = {
                line_num for node in method_lines.values() for line_num in node['line']
            }

            # Lines not in the dependent slice
            dependent_line_nums = {line_num for line_num, _ in dependent_lines}
            unrelated_lines = all_method_lines - dependent_line_nums

            # Exclude the method definition line and other declaration lines
            for node in method_lines.values():
                if "(" in node['text'] and ")" in node['text'] and "{" in node['text']:
                    if node['line'][0] in unrelated_lines:
                        unrelated_lines.remove(node['line'][0])

            # Analyze unrelated lines for additional variable dependencies
            if unrelated_lines:
                print("\nAnalyzing unrelated lines for additional variable dependencies:")
                analyze_unrelated_lines(unrelated_lines, slicing_tool.pdg_nodes, slicing_tool, other_slicing)

            # Find duplicate lines
            def find_duplicates(main_slicing, other_slicing):
                """
                Find the common lines between main_slicing and other_slicing.
                """
                main_lines = {line for sublist in main_slicing for line in sublist}
                other_lines = {line for sublist in other_slicing for line in sublist}

                duplicate = sorted(main_lines & other_lines)
                return duplicate

            # Example usage after running the backward slicing process
            duplicate_lines = find_duplicates(main_slicing, other_slicing)

            # Print the summary of slicing results
            print("\nSummary of slicing results:")
            print(f"main slicing: {main_slicing}")
            print(f"other slicing: {other_slicing}")
            print(f"Duplicate lines: {duplicate_lines}")

            # Save the results for this variable
            results[line][variable] = {
                'main_slicing': main_slicing,
                'other_slicing': other_slicing,
                'duplicate_lines': duplicate_lines
            }

    # Return the results instead of writing to a file
    print("results results", results)
    return results


if __name__ == "__main__":
    input_file_path = "test\\f.java"

    filtered_used_variables_dict = main_output(input_file_path)

    slicing_results = main(input_file_path, filtered_used_variables_dict)

    # print("\nFinal Slicing Results:")
    # print(json.dumps(slicing_results, indent=4))
