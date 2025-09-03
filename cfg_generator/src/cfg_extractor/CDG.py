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

class CDGExtractorVisitor(JavaParserVisitor):
    def __init__(self):
        super().__init__()
        self.node_id_counter = 0
        self.cdg_nodes = {}
        self.method_nodes = {}
        self.current_function = None
        self.current_class = None
        self.previous_nodes = []
        self.method_node_id = None
        self.last_method_node = None
        self.current_control_node = None
        self.graph = nx.DiGraph()

    def create_node(self, ctx, node_type):

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

        previous_nodes_with_types = []
        for prev_node in control_deps:
            if prev_node is not None:
                previous_nodes_with_types.append({"id": prev_node, "type": "control"})

        self.cdg_nodes[node_id] = {
            "node id": node_id,
            "line": [start_line],
            "text": node_text,
            "type": node_type,
            "previous node": previous_nodes_with_types,
            "next node": [],
            "function name": self.current_function,
        }

        self.graph.add_node(node_id,
                            text=node_text,
                            type=node_type,
                            line=start_line,
                            function=self.current_function,
                            )

        if self.current_function:
            if self.current_function not in self.method_nodes:
                self.method_nodes[self.current_function] = []
            self.method_nodes[self.current_function].append(self.cdg_nodes[node_id])

        if node_type in ['control', 'decision', 'method_control']:
            self.previous_nodes.append({"id": node_id, "type": node_type})
            self.current_control_node = node_id

        for prev_node in control_deps:
            self.add_edge(prev_node, node_id)

        return node_id

    def add_edge(self, from_node, to_node):

        if from_node in self.cdg_nodes:
            edge_info = {"id": to_node}

            if edge_info not in self.cdg_nodes[from_node]['next node']:
                self.cdg_nodes[from_node]['next node'].append(edge_info)

        if to_node in self.cdg_nodes:
            previous_node_info = {"id": from_node}
            if previous_node_info not in self.cdg_nodes[to_node]['previous node']:
                self.cdg_nodes[to_node]['previous node'].append(previous_node_info)

        self.graph.add_edge(from_node, to_node)

    def create_node_id_by_type(self, node_type):
        for node_id in reversed(self.cdg_nodes):
            if self.cdg_nodes[node_id]['type'] == node_type:
                return node_id
        return None

    def visitMethodDeclaration(self, ctx: JavaParser.MethodDeclarationContext):

        if hasattr(ctx, 'annotation') and ctx.annotation():
            for annotation in ctx.annotation():
                self.visit(annotation)

        method_signature = self.get_method_signature(ctx)
        self.current_function = method_signature

        node_id = self.create_node(ctx, "method_control")
        self.method_nodes[method_signature] = [self.cdg_nodes[node_id]]
        self.method_node_id = node_id
        self.last_method_node = node_id

        if ctx.methodBody():
            self.visit(ctx.methodBody())

        self.current_function = None
        self.current_control_node = None
        self.method_node_id = None
        self.previous_nodes = []

        return self.cdg_nodes.get(node_id, None)

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

        return ctx.getText()

    def visitAnnotation(self, ctx: JavaParser.AnnotationContext):

        pass

    def visitMethodHeader(self, ctx: JavaParser.MethodHeaderContext):

        return self.visit(ctx.methodDeclarator())

    def visitMethodDeclarator(self, ctx: JavaParser.MethodDeclaratorContext):

        return ctx.Identifier().getText()

    def visitBlock(self, ctx: JavaParser.BlockContext):
        if ctx.blockStatements() is not None:
            self.visit(ctx.blockStatements())

    def visitBlockStatements(self, ctx: JavaParser.BlockStatementsContext):

        if ctx.blockStatement() is not None:
            for block_stmt in ctx.blockStatement():
                original_control_node = self.current_control_node

                self.visit(block_stmt)

                if self.current_control_node is None and self.method_node_id is not None:
                    self.current_control_node = self.method_node_id

                self.current_control_node = original_control_node

    def visitIfThenStatement(self, ctx: JavaParser.IfThenStatementContext):

        decision_node_id = self.create_node(ctx, "decision")

        original_control_node = self.current_control_node
        self.current_control_node = decision_node_id
        self.visit(ctx.statement())

        self.current_control_node = original_control_node

        return decision_node_id

    def visitIfThenElseStatement(self, ctx: JavaParser.IfThenElseStatementContext):

        decision_node_id = self.create_node(ctx, "decision")

        original_control_node = self.current_control_node
        self.current_control_node = decision_node_id

        self.visit(ctx.statementNoShortIf())

        if ctx.statement():
            if not isinstance(ctx.statement(), JavaParser.IfThenElseStatementContext):
                self.visit(ctx.statement())
            else:
                self.visit(ctx.statement())

        self.current_control_node = original_control_node
        return decision_node_id

    def visitExpressionStatement(self, ctx: JavaParser.ExpressionStatementContext):

        expr_text = ctx.getText()

        if "System.out.print" in expr_text or "System.out.println" in expr_text:
            node_id = self.create_node(ctx, "output")
        else:
            node_id = self.create_node(ctx, "expression")

        if self.current_control_node is not None:
            self.add_edge(self.current_control_node, node_id)

        return node_id

    def visitSwitchStatement(self, ctx: JavaParser.SwitchStatementContext):
        node_id = self.create_node(ctx, "control")
        self.visit(ctx.switchBlock())
        return node_id

    def visitSwitchBlock(self, ctx: JavaParser.SwitchBlockContext):
        for switch_group in ctx.switchBlockStatementGroup():
            self.visit(switch_group)

    def visitSwitchBlockStatementGroup(self, ctx: JavaParser.SwitchBlockStatementGroupContext):
        self.visit(ctx.blockStatements())

    def visitBasicForStatement(self, ctx: JavaParser.BasicForStatementContext):

        node_id = self.create_node(ctx, "control")

        self.visit(ctx.statement())
        return node_id

    def visitWhileStatement(self, ctx: JavaParser.WhileStatementContext):
        node_id = self.create_node(ctx, "control")

        # self.add_edge(node_id, node_id)

        original_control_node = self.current_control_node
        self.current_control_node = node_id

        previous_node = None

        statement = ctx.statement()
        if hasattr(statement, "block") and statement.block() is not None:
            for block_stmt in statement.block().blockStatements().blockStatement():
                current_node_id = self.visit(block_stmt)

                if previous_node is not None:
                    self.add_edge(previous_node, current_node_id)

                previous_node = current_node_id
        else:
            current_node_id = self.visit(statement)
            if previous_node is not None:
                self.add_edge(previous_node, current_node_id)
            previous_node = current_node_id

        if previous_node is not None:
            self.add_edge(previous_node, node_id)

        self.current_control_node = original_control_node
        return node_id

    def visitDoStatement(self, ctx: JavaParser.DoStatementContext):
        node_id = self.create_node(ctx, "control")
        self.visit(ctx.statement())
        return node_id

    def visitTryStatement(self, ctx: JavaParser.TryStatementContext):
        original_control_node = self.current_control_node

        node_id = self.create_node(ctx, "control")

        self.current_control_node = node_id

        self.visit(ctx.block())
        if ctx.catches():
            for catch in ctx.catches().catchClause():
                self.visit(catch)
        if ctx.finallyBlock():
            self.visit(ctx.finallyBlock().block())

        self.current_control_node = original_control_node

        return node_id

    def visitCatches(self, ctx: JavaParser.CatchesContext):
        for catch in ctx.catchClause():
            self.visit(catch)

    def visitCatchClause(self, ctx: JavaParser.CatchClauseContext):
        node_id = self.create_node(ctx, "control")
        self.visit(ctx.block())
        return node_id

    def visitEnhancedForStatement(self, ctx: JavaParser.EnhancedForStatementContext):
        var_declarators = ctx.localVariableDeclaration().variableDeclaratorList().variableDeclarator()

        node_id = self.create_node(ctx, "control")
        self.visit(ctx.statement())

        return node_id

    def visitLocalVariableDeclarationStatement(self, ctx: JavaParser.LocalVariableDeclarationStatementContext):

        node_id = self.create_node(ctx, "declaration")
        return node_id

    def visitBreakStatement(self, ctx: JavaParser.BreakStatementContext):
        node_id = self.create_node(ctx, "break")
        return node_id

    def visitLocalVariableDeclaration(self, ctx: JavaParser.LocalVariableDeclarationContext):
        var_name = ctx.getText()
        node_id = self.create_node(ctx, "declaration")
        return node_id

    def visitContinueStatement(self, ctx: JavaParser.ContinueStatementContext):
        node_id = self.create_node(ctx, "continue")
        return node_id

    def visitThrowStatement(self, ctx: JavaParser.ThrowStatementContext):
        node_id = self.create_node(ctx, "throw")
        return node_id

    def visitReturnStatement(self, ctx: JavaParser.ReturnStatementContext):

        node_id = self.create_node(ctx, "return")

        if self.current_control_node is not None:
            self.add_edge(self.current_control_node, node_id)
        elif self.method_node_id is not None:
            self.add_edge(self.method_node_id, node_id)

        return node_id

    def visitEmptyStatement_(self, ctx: JavaParser.EmptyStatement_Context):
        node_id = self.create_node(ctx, "empty")
        return node_id


def generate_method_graph(cdg_nodes, method_nodes, method_name, output_dir):
    if not isinstance(method_name, str):
        print(f"Invalid method_name: {method_name}. Expected a string. Using default 'unknown_method'.")
        method_name = "unknown_method"
    if not method_name:
        print(f"Empty method_name provided. Using default 'unknown_method'.")
        method_name = "unknown_method"

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    dot = Digraph(comment=f'CDG for Method: {method_name}', format='png')
    for node_info in method_nodes.get(method_name, []):
        node_id = node_info['node id']
        label = f"{node_info['text']}\nType: {node_info['type']}\nLines: {node_info['line']}"
        dot.node(str(node_id), label=label)
    for node_info in method_nodes.get(method_name, []):
        node_id = node_info['node id']
        for edge in node_info['next node']:
            style = 'solid'
            label = edge.get("variable", "")
            dot.edge(str(node_id), str(edge['id']), style=style, label=label)
    output_path = os.path.join(output_dir, f"{method_name}")
    # dot.render(output_path, format="png", cleanup=True)  # Commented out to prevent saving PNG
    print(f"Graph for method '{method_name}' would be saved as {output_path}.png (saving disabled)")


def main(input_file_path, cdg_extractor_visitor):
    # Extract file name without extension
    file_name_without_extension = os.path.splitext(os.path.basename(input_file_path))[0]

    # Configure output directory
    output_dir = f"{file_name_without_extension}_cdg"
    method_nodes_list = []
    # Parse the Java file
    input_stream = FileStream(input_file_path, encoding='utf-8')
    lexer = JavaLexer(input_stream)
    stream = CommonTokenStream(lexer)
    parser = JavaParser(stream)
    tree = parser.compilationUnit()
    cdg_extractor_visitor.visit(tree)

    # Output the list of nodes for each method
    method_names = set(cdg_extractor_visitor.method_nodes.keys())
    for method_name in method_names:
        method_node_list = cdg_extractor_visitor.method_nodes[method_name]
        method_nodes_list.append(method_node_list)  # اضافه کردن لیست هر متد به لیست اصلی

        generate_method_graph(cdg_extractor_visitor.cdg_nodes, cdg_extractor_visitor.method_nodes, method_name,
                              output_dir)
    print(method_nodes_list)
    return method_nodes_list



if __name__ == "__main__":
    # Specify the path to your input Java file
    input_file_path = "test\\f.java"

    visitor = CDGExtractorVisitor()

    main(input_file_path, visitor)

