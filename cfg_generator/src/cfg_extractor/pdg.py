import re
from antlr4 import InputStream, CommonTokenStream, FileStream
from graphviz import Digraph
from cfg_generator.src.antlr.gen.JavaLexer import JavaLexer
from cfg_generator.src.antlr.gen.JavaParser import JavaParser
from cfg_generator.src.antlr.gen.JavaParserVisitor import JavaParserVisitor
from typing import List, Tuple
import os
from pathlib import Path
import networkx as nx

class PDGExtractorVisitor(JavaParserVisitor):
    def __init__(self):
        super().__init__()
        self.node_id_counter = 0
        self.pdg_nodes = {}
        self.method_nodes = {}  # Dictionary to store nodes per method
        self.current_function = None
        self.current_class = None
        self.previous_nodes = []
        self.defined_vars = {}
        self.method_node_id = None
        self.last_method_node = None
        self.current_control_node = None
        self.graph = nx.DiGraph()

    def create_node(self, ctx, node_type, defined_var=None, used_vars=None):
        input_lines = ctx.start.getInputStream().strdata.splitlines()
        start_line = ctx.start.line

        node_text = input_lines[start_line - 1].strip()
        if node_text.startswith('@'):
            for i in range(start_line, len(input_lines)):
                node_text = input_lines[i].strip()
                if not node_text.startswith('@'):
                    break

        node_id = self.node_id_counter
        self.node_id_counter += 1

        if self.current_control_node is not None:
            control_deps = [self.current_control_node]
        else:
            control_deps = [self.method_node_id] if self.method_node_id is not None else []

        data_deps = []
        if used_vars:
            for var in used_vars:
                if var in self.defined_vars:
                    data_deps.append((self.defined_vars[var], var))

        if defined_var:
            for var in defined_var:
                if var in self.defined_vars:
                    self.add_edge(self.defined_vars[var], node_id, "data", variable=var)
                self.defined_vars[var] = node_id

        previous_nodes_with_types = []
        for prev_node in control_deps:
            if prev_node is not None:
                previous_nodes_with_types.append({"id": prev_node, "type": "control"})
        for prev_node, var in data_deps:
            if prev_node is not None:
                previous_nodes_with_types.append({"id": prev_node, "type": "data", "variable": var})

        self.pdg_nodes[node_id] = {
            "node id": node_id,
            "line": [start_line],
            "text": node_text,
            "type": node_type,
            "previous node": previous_nodes_with_types,
            "next node": [],
            "function name": self.current_function,
            "def": defined_var or [],
            "use": used_vars or []
        }

        self.graph.add_node(node_id,
                            text=node_text,
                            type=node_type,
                            line=start_line,
                            function=self.current_function,
                            defined_vars=defined_var or [],
                            used_vars=used_vars or [])

        if self.current_function:
            if self.current_function not in self.method_nodes:
                self.method_nodes[self.current_function] = []
            self.method_nodes[self.current_function].append(self.pdg_nodes[node_id])

        if node_type in ['control', 'decision', 'method_control']:
            self.previous_nodes.append({"id": node_id, "type": node_type})
            self.current_control_node = node_id

        for prev_node in control_deps:
            self.add_edge(prev_node, node_id, "control")
        for prev_node, var in data_deps:
            self.add_edge(prev_node, node_id, "data", variable=var)

        return node_id
    def add_edge(self, from_node, to_node, edge_type, variable=None):
        """
        Add an edge between two nodes, specifying the edge type and optionally the variable causing the dependency.
        """
        if from_node in self.pdg_nodes:
            edge_info = {"id": to_node, "type": edge_type}
            if variable:
                edge_info["variable"] = variable  # Add variable causing the dependency
            # Only add the edge if it does not already exist
            if edge_info not in self.pdg_nodes[from_node]['next node']:
                self.pdg_nodes[from_node]['next node'].append(edge_info)

        # Update the previous node information for the target node
        if to_node in self.pdg_nodes:
            previous_node_info = {"id": from_node, "type": edge_type}
            if variable:
                previous_node_info["variable"] = variable
            if previous_node_info not in self.pdg_nodes[to_node]['previous node']:
                self.pdg_nodes[to_node]['previous node'].append(previous_node_info)

        # Add the edge to the graph
        edge_attrs = {"type": edge_type}
        if variable:
            edge_attrs["variable"] = variable
        self.graph.add_edge(from_node, to_node, **edge_attrs)

    def create_node_id_by_type(self, node_type):
        for node_id in reversed(self.pdg_nodes):
            if self.pdg_nodes[node_id]['type'] == node_type:
                return node_id
        return None

    def visitMethodDeclaration(self, ctx: JavaParser.MethodDeclarationContext):
        """
        Visits a method declaration and extracts its information into the PDG.
        """
        # Ignore annotations such as @Override
        if hasattr(ctx, 'annotation') and ctx.annotation():
            for annotation in ctx.annotation():
                self.visit(annotation)  # This will skip creating nodes for annotations

        # Retrieve the full signature of the method
        method_signature = self.get_method_signature(ctx)
        self.current_function = method_signature

        # Create a method control node for this method
        node_id = self.create_node(ctx, "method_control", defined_var=None, used_vars=None)
        self.method_nodes[method_signature] = [self.pdg_nodes[node_id]]
        self.method_node_id = node_id
        self.last_method_node = node_id

        # Visit the method body
        if ctx.methodBody():
            self.visit(ctx.methodBody())

        # Reset the state for the current function
        self.current_function = None
        self.current_control_node = None
        self.method_node_id = None
        self.previous_nodes = []  # Reset previous nodes for the next method

        return self.pdg_nodes.get(node_id, None)

    def get_method_signature(self, ctx: JavaParser.MethodDeclarationContext) -> str:
        """
        Extracts the method signature including method name and parameter types/names, excluding return type.
        """
        # Get the method name
        method_name = ctx.methodHeader().methodDeclarator().Identifier().getText()

        # Get the parameter list
        parameters = []
        arg_index = 0
        formal_parameter_list = ctx.methodHeader().methodDeclarator().formalParameterList()

        def _safe_param(param):
            nonlocal arg_index
            param_type = param.unannType().getText() if param.unannType() else "unknown"
            is_varargs = "..." in param.getText()
            if is_varargs:
                param_type = param_type + "..."
            if param.variableDeclaratorId() and param.variableDeclaratorId().Identifier():
                param_name = param.variableDeclaratorId().Identifier().getText()
            else:
                arg_index += 1
                param_name = f"arg{arg_index}"
            parameters.append(f"{param_type} {param_name}")

        if formal_parameter_list:
            for param in formal_parameter_list.formalParameter():
                _safe_param(param)

        # Construct the method signature
        return f"{method_name}({', '.join(parameters)})"

    def get_type_from_context(self, ctx) -> str:
        """
        Extracts the type from the context node.
        """
        return ctx.getText()

    def visitAnnotation(self, ctx: JavaParser.AnnotationContext):
        """
        Skips processing annotations such as @Override.
        """
        # Do nothing, simply skip annotations
        pass

    def visitMethodHeader(self, ctx: JavaParser.MethodHeaderContext):
        """
        Visits the method header to retrieve the method's name.
        """
        return self.visit(ctx.methodDeclarator())

    def visitMethodDeclarator(self, ctx: JavaParser.MethodDeclaratorContext):
        """
        Visits the method declarator to extract the method's identifier.
        """
        return ctx.Identifier().getText()

    def visitBlock(self, ctx: JavaParser.BlockContext):
        if ctx.blockStatements() is not None:
            self.visit(ctx.blockStatements())

    def visitBlockStatements(self, ctx: JavaParser.BlockStatementsContext):
        """
        Visit all block statements, ensuring nodes outside control structures are linked to the method control node.
        """
        if ctx.blockStatement() is not None:
            for block_stmt in ctx.blockStatement():
                # Temporarily save the current control node
                original_control_node = self.current_control_node

                # Visit the block statement
                self.visit(block_stmt)

                # If the control node hasn't changed, ensure it's connected to the method control node
                if self.current_control_node is None and self.method_node_id is not None:
                    self.current_control_node = self.method_node_id

                # Restore the original control node
                self.current_control_node = original_control_node

    def visitIfThenStatement(self, ctx: JavaParser.IfThenStatementContext):
        """
        Handles simple 'if' statements without an 'else'.
        """
        # Extract condition variables from the 'if' expression
        condition_vars = self.extract_variables_from_expression(ctx.expression().getText())

        # Create a decision/control node for the 'if' condition
        decision_node_id = self.create_node(ctx, "decision", None, condition_vars)

        # Temporarily set this decision node as the current control node
        original_control_node = self.current_control_node
        self.current_control_node = decision_node_id

        # Visit the 'then' block
        self.visit(ctx.statement())

        # Restore the previous control node after visiting
        self.current_control_node = original_control_node

        return decision_node_id

    def visitIfThenElseStatement(self, ctx: JavaParser.IfThenElseStatementContext):
        """
        Handles 'if-then-else' statements, ensuring proper control flow handling for 'else if' and 'else'.
        """
        # Extract condition variables for the primary 'if'
        condition_vars = self.extract_variables_from_expression(ctx.expression().getText())

        # Create a decision node for the 'if' condition
        decision_node_id = self.create_node(ctx, "decision", None, condition_vars)

        # Temporarily set this decision node as the current control node
        original_control_node = self.current_control_node
        self.current_control_node = decision_node_id

        # Visit the 'then' block
        self.visit(ctx.statementNoShortIf())

        # Handle the 'else' block
        if ctx.statement():
            # Create an 'else' node for the 'else' block
            else_node_id = self.create_node(ctx.statement(), "else", None, None)

            # Set the 'else' node as the current control node
            self.current_control_node = else_node_id

            # Visit the 'else' block
            self.visit(ctx.statement())

        # Restore the previous control node after visiting
        self.current_control_node = original_control_node

        return decision_node_id

    def visitExpressionStatement(self, ctx: JavaParser.ExpressionStatementContext):
        """
        Visit an expression statement and handle output nodes for print-like expressions.
        """
        expr_text = ctx.getText()
        defined_var, used_vars = self.extract_def_use(expr_text)

        # Check if it's a System.out.print or System.out.println call
        if "System.out.print" in expr_text or "System.out.println" in expr_text:
            # Treat this as an output node
            node_id = self.create_node(ctx, "output", None, used_vars)
        else:
            # For other expressions, treat normally
            node_id = self.create_node(ctx, "expression", defined_var, used_vars)

        # Add a control dependency to the current control node
        if self.current_control_node is not None:
            self.add_edge(self.current_control_node, node_id, "control")

        return node_id

    def visitSwitchStatement(self, ctx: JavaParser.SwitchStatementContext):
        condition_vars = [ctx.expression().getText()]
        node_id = self.create_node(ctx, "control", None, condition_vars)
        self.visit(ctx.switchBlock())
        return node_id

    def visitSwitchBlock(self, ctx: JavaParser.SwitchBlockContext):
        for switch_group in ctx.switchBlockStatementGroup():
            self.visit(switch_group)

    def visitSwitchBlockStatementGroup(self, ctx: JavaParser.SwitchBlockStatementGroupContext):
        self.visit(ctx.blockStatements())

    def visitBasicForStatement(self, ctx: JavaParser.BasicForStatementContext):
        # Process the initialization part to capture defined variables
        init_expr = ctx.forInit()
        defined_vars, used_vars = [], []

        if init_expr:
            # Check if it's a local variable declaration
            if init_expr.localVariableDeclaration():
                for declarator in init_expr.localVariableDeclaration().variableDeclaratorList().variableDeclarator():
                    var_name = declarator.variableDeclaratorId().Identifier().getText()
                    defined_vars.append(var_name)  # Define the variable in the initialization
            else:
                # Handle other types of initialization (expression)
                init_text = init_expr.getText()
                defined_vars, init_used_vars = self.extract_def_use(init_text)
                used_vars.extend(init_used_vars)

        # Process the condition part to capture used variables
        condition_expr = ctx.expression()
        if condition_expr:
            condition_vars = self.extract_variables_from_expression(condition_expr.getText())
            used_vars.extend(condition_vars)

        # Process the update part to capture used variables
        update_expr = ctx.forUpdate()
        if update_expr:
            update_vars = self.extract_variables_from_expression(update_expr.getText())
            used_vars.extend(update_vars)

        # Create the node for the 'for' control structure with detected def/use dependencies
        node_id = self.create_node(ctx, "control", defined_vars, used_vars)

        # Visit the loop body to ensure nested statements are processed
        self.visit(ctx.statement())
        return node_id


    def visitWhileStatement(self, ctx: JavaParser.WhileStatementContext):
        condition_vars = self.extract_variables_from_expression(ctx.expression().getText())
        node_id = self.create_node(ctx, "control", None, condition_vars)

        self.add_edge(node_id, node_id, "control")

        original_control_node = self.current_control_node
        self.current_control_node = node_id

        previous_node = None

        statement = ctx.statement()
        if hasattr(statement, "block") and statement.block() is not None:
            for block_stmt in statement.block().blockStatements().blockStatement():
                current_node_id = self.visit(block_stmt)

                if previous_node is not None:
                    self.add_edge(previous_node, current_node_id, "control")

                previous_node = current_node_id
        else:
            current_node_id = self.visit(statement)
            if previous_node is not None:
                self.add_edge(previous_node, current_node_id, "control")
            previous_node = current_node_id

        if previous_node is not None:
            self.add_edge(previous_node, node_id, "control")


        self.current_control_node = original_control_node
        return node_id

    def visitDoStatement(self, ctx: JavaParser.DoStatementContext):
        node_id = self.create_node(ctx, "control")
        self.visit(ctx.statement())
        return node_id

    def visitTryStatement(self, ctx: JavaParser.TryStatementContext):
        # Temporarily store the original control node to restore later
        original_control_node = self.current_control_node

        # Create a new node for the try block, with its own isolated control context
        node_id = self.create_node(ctx, "control")

        # Set the current control node to the try node to isolate its control scope
        self.current_control_node = node_id

        # Visit the try block and its catch clauses, maintaining control within this scope
        self.visit(ctx.block())
        if ctx.catches():
            for catch in ctx.catches().catchClause():
                self.visit(catch)
        if ctx.finallyBlock():
            self.visit(ctx.finallyBlock().block())

        # Restore the previous control node once we exit the try-catch-finally context
        self.current_control_node = original_control_node

        return node_id

    def visitCatches(self, ctx: JavaParser.CatchesContext):
        for catch in ctx.catchClause():
            self.visit(catch)

    def visitCatchClause(self, ctx: JavaParser.CatchClauseContext):
        node_id = self.create_node(ctx, "control")
        self.visit(ctx.block())
        return node_id

    def extract_variables_from_expression(self, expr_text: str) -> List[str]:
        """
        Extracts variable names from an expression, ignoring string literals and reserved keywords.
        """
        # Define reserved keywords, method names, and system methods that should not be considered as variables
        reserved_keywords = {
            "if", "else", "while", "for", "return", "try", "catch", "throw",
            "switch", "true", "false", "null", "new", "instanceof", "void"
        }
        system_methods = {
            "System", "out", "println", "print", "Scanner", "nextInt", "nextLine"
        }

        # Remove all string literals (content between double quotes)
        expr_text_without_strings = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', "", expr_text)

        # Match variable names (identifiers) outside of the string literals
        variables = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', expr_text_without_strings)

        # Filter out reserved keywords and system methods
        return [var for var in variables if
                var not in reserved_keywords and var not in system_methods and not var.isdigit()]

    def extract_def_use(self, expr_text: str) -> Tuple[List[str], List[str]]:
        """
        Extracts the 'def' and 'use' variables from an expression, handling assignment and ignoring string literals.
        """
        # Remove all string literals from the expression
        expr_text_without_strings = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', "", expr_text)

        # Initialize lists for defined and used variables
        defined_vars = []
        used_vars = []

        # Check for assignment in the expression after removing string literals
        if "=" in expr_text_without_strings:
            # Split into left-hand side (lhs) and right-hand side (rhs) around the first '='
            lhs, rhs = expr_text_without_strings.split("=", 1)

            # Extract variables from the left-hand side (definition)
            lhs_vars = self.extract_variables_from_expression(lhs)

            # Extract variables from the right-hand side (usage)
            rhs_vars = self.extract_variables_from_expression(rhs)

            defined_vars.extend(lhs_vars)
            used_vars.extend(rhs_vars)
        else:
            # In case of non-assignment, return only used variables
            used_vars.extend(self.extract_variables_from_expression(expr_text_without_strings))

        # Handle increment/decrement and compound assignment operators
        increment_decrement_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(\+\+|--|\+=|-=|\*=|/=|%=)'
        matches = re.findall(increment_decrement_pattern, expr_text_without_strings)

        for var, _ in matches:
            if var not in defined_vars:
                defined_vars.append(var)  # Add to defined variables
            if var not in used_vars:
                used_vars.append(var)  # Ensure it's also in used variables

        return defined_vars, used_vars

    def visitEnhancedForStatement(self, ctx: JavaParser.EnhancedForStatementContext):
        # Extract the variable(s) involved in the loop
        var_declarators = ctx.localVariableDeclaration().variableDeclaratorList().variableDeclarator()

        # If there's only one variable, you can extract it directly
        if len(var_declarators) == 1:
            var_name = var_declarators[0].variableDeclaratorId().Identifier().getText()
        else:
            # If there are multiple variables, handle accordingly (adjust logic if needed)
            var_name = [declarator.variableDeclaratorId().Identifier().getText() for declarator in var_declarators]

        # Extract the variables used in the expression (right-hand side of the loop)
        used_vars = self.extract_variables_from_expression(ctx.expression().getText())

        # Create a node for the loop control
        node_id = self.create_node(ctx, "control", var_name, used_vars)

        # Visit the loop body
        self.visit(ctx.statement())

        return node_id

    def visitLocalVariableDeclarationStatement(self, ctx: JavaParser.LocalVariableDeclarationStatementContext):
        declarators = ctx.localVariableDeclaration().variableDeclaratorList().variableDeclarator()
        defined_vars = []
        used_vars = []

        for declarator in declarators:

            var_name = declarator.variableDeclaratorId().Identifier().getText()
            defined_vars.append(var_name)

            init_expr = declarator.variableInitializer()
            if init_expr:
                expr_text = init_expr.getText()
                _, init_used_vars = self.extract_def_use(expr_text)
                used_vars.extend(init_used_vars)

        node_id = self.create_node(ctx, "declaration", defined_vars, used_vars)
        return node_id

    def visitBreakStatement(self, ctx: JavaParser.BreakStatementContext):
        node_id = self.create_node(ctx, "break")
        return node_id

    def visitLocalVariableDeclaration(self, ctx: JavaParser.LocalVariableDeclarationContext):
        var_name = ctx.getText()
        node_id = self.create_node(ctx, "declaration", [var_name], [])
        return node_id

    def visitContinueStatement(self, ctx: JavaParser.ContinueStatementContext):
        node_id = self.create_node(ctx, "continue")
        return node_id

    def visitThrowStatement(self, ctx: JavaParser.ThrowStatementContext):
        node_id = self.create_node(ctx, "throw")
        return node_id

    def visitReturnStatement(self, ctx: JavaParser.ReturnStatementContext):
        """
        Processes return statements and ensures correct control dependencies.
        """
        # Extract used variables from the return expression
        used_vars = self.extract_variables_from_expression(ctx.expression().getText()) if ctx.expression() else []

        # Create a return node
        node_id = self.create_node(ctx, "return", None, used_vars)

        # Determine the control dependency
        if self.current_control_node is not None:
            # Connect to the current control node if one exists
            self.add_edge(self.current_control_node, node_id, "control")
        elif self.method_node_id is not None:
            # If no control node, connect to the method's control node
            self.add_edge(self.method_node_id, node_id, "control")

        return node_id

    def visitEmptyStatement_(self, ctx: JavaParser.EmptyStatement_Context):
        node_id = self.create_node(ctx, "empty")
        return node_id

def generate_method_graph(pdg_nodes, method_nodes, method_name, output_dir):
    """
    Generate a graph for a specific method and save it.
    """
    if not isinstance(method_name, str):
        print(f"Invalid method_name: {method_name}. Expected a string. Using default 'unknown_method'.")
        method_name = "unknown_method"
    if not method_name:
        print(f"Empty method_name provided. Using default 'unknown_method'.")
        method_name = "unknown_method"

    # Create the output directory if it does not exist
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Initialize the graph
    dot = Digraph(comment=f'PDG for Method: {method_name}', format='png')

    # Add nodes to the graph
    for node_info in method_nodes.get(method_name, []):
        node_id = node_info['node id']
        label = f"{node_info['text']}\nType: {node_info['type']}\nLines: {node_info['line']}"
        dot.node(str(node_id), label=label)

    # Add edges to the graph
    for node_info in method_nodes.get(method_name, []):
        node_id = node_info['node id']
        for edge in node_info['next node']:
            style = 'solid' if edge['type'] == 'control' else 'dashed'
            label = edge.get("variable", "")  # Label for data dependency variable
            dot.edge(str(node_id), str(edge['id']), style=style, label=label)

    # Set the full path for the output file
    output_path = os.path.join(output_dir, f"{method_name}")

    # Render and save the graph (commented out to prevent saving PNG)
    # dot.render(output_path, format="png", cleanup=True)  # cleanup=True prevents extra files
    print(f"Graph for method '{method_name}' would be saved as {output_path}.png (saving disabled)")



def main(input_file_path, pdg_extractor_visitor):
    # Extract file name without extension
    file_name_without_extension = os.path.splitext(os.path.basename(input_file_path))[0]

    # Configure output directory
    output_dir = f"{file_name_without_extension}_pdg"

    # Parse the Java file
    input_stream = FileStream(input_file_path, encoding='utf-8')
    lexer = JavaLexer(input_stream)
    stream = CommonTokenStream(lexer)
    parser = JavaParser(stream)
    tree = parser.compilationUnit()
    pdg_extractor_visitor.visit(tree)

    # Output the list of nodes for each method
    method_names = set(pdg_extractor_visitor.method_nodes.keys())
    for method_name in method_names:
        print(f"Processing method: {method_name}")
        # Display the method's node list in the required format
        method_node_list = pdg_extractor_visitor.method_nodes[method_name]
        for node in method_node_list:
            print({
                "Node ID": node['node id'],
                "line": node['line'],
                "text": node['text'],
                "type": node['type'],
                "previous node": node['previous node'],
                "next node": node['next node'],
                "function name": node['function name'],
                "def": node['def'],
                "use": node['use']
            })
            print("-" * 50)

        # Generate graph for each method
        generate_method_graph(pdg_extractor_visitor.pdg_nodes, pdg_extractor_visitor.method_nodes, method_name,
                              output_dir)


if __name__ == "__main__":
    # Specify the path to your input Java file
    input_file_path = "G:\\OpenUnderstand\\cfg_generator\\test\\f.java"

    visitor = PDGExtractorVisitor()

    main(input_file_path, visitor)




