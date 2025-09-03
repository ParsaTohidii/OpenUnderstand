import re
from antlr4 import InputStream, CommonTokenStream, FileStream
from cfg_generator.src.antlr.gen.JavaLexer import JavaLexer
from cfg_generator.src.antlr.gen.JavaParser import JavaParser
from cfg_generator.src.antlr.gen.JavaParserVisitor import JavaParserVisitor
from pathlib import Path
import os
import json


class JavaCodeAnalyzer(JavaParserVisitor):
    def __init__(self):
        super().__init__()
        self.local_variables = set()
        self.global_variables = {}
        self.defined_variables = set()
        self.result = []
        self.current_method_name = None
        self.current_method_is_void = False
        self.current_method_variables_used_in_calls = set()
        self.method_parameters = set()

    def visitMethodDeclaration(self, ctx: JavaParser.MethodDeclarationContext):
        self.local_variables = set()
        method_name = ctx.methodHeader().methodDeclarator().Identifier().getText()
        parameters = []
        self.method_parameters.clear()
        formal_parameter_list = ctx.methodHeader().methodDeclarator().formalParameterList()
        arg_index = 0

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
            self.method_parameters.add(param_name)
            self.defined_variables.add(param_name)

        if formal_parameter_list:
            for param in formal_parameter_list.formalParameter():
                _safe_param(param)

        method_signature = f"{method_name}({', '.join(parameters)})"
        return_type = ctx.methodHeader().result().getText()
        self.current_method_name = method_name
        self.current_method_is_void = return_type == "void"
        self.current_method_variables_used_in_calls = set()
        self.result.append({
            "line": ctx.start.line,
            "method_name": method_signature,  # استفاده از امضای کامل به جای فقط method_name
            "parameters": parameters,
        })
        super().visitMethodDeclaration(ctx)
        if self.current_method_is_void and self.current_method_variables_used_in_calls:
            self.result.append({
                "line": ctx.start.line,
                "method_name": method_signature,  # استفاده از امضای کامل
                "void_method_output_variables": list(self.current_method_variables_used_in_calls)
            })
        return None
    def visitLocalVariableDeclaration(self, ctx: JavaParser.LocalVariableDeclarationContext):
        variables = []
        if ctx.variableDeclaratorList():
            for var_decl in ctx.variableDeclaratorList().variableDeclarator():
                var_name = var_decl.variableDeclaratorId().Identifier().getText()
                variables.append(var_name)
                self.local_variables.add(var_name)
                self.defined_variables.add(var_name)
        if variables:
            self.result.append({
                "line": ctx.start.line,
                "local_variables": variables
            })
        return super().visitLocalVariableDeclaration(ctx)

    def visitAssignment(self, ctx: JavaParser.AssignmentContext):
        left_text = ctx.leftHandSide().getText()
        right_text = ctx.expression().getText() if ctx.expression() else ""
        all_used = self.extract_used_variables(left_text + " " + right_text)
        global_vars_in_line = []
        for var in all_used:
            if var not in self.defined_variables and var not in self.local_variables:
                if var not in self.global_variables:
                    self.global_variables[var] = ctx.start.line
                if var not in global_vars_in_line:
                    global_vars_in_line.append(var)
        if global_vars_in_line:
            self.result.append({
                "line": ctx.start.line,
                "global_variable": global_vars_in_line[0],
                "used_variables": global_vars_in_line
            })
        return super().visitAssignment(ctx)

    def visitExpression(self, ctx: JavaParser.ExpressionContext):
        if ctx.getText():
            used_vars = self.extract_used_variables(ctx.getText())
            for var in used_vars:
                if (var not in self.defined_variables and
                        var not in self.local_variables and
                        var not in self.method_parameters and
                        not self.is_keyword(var)):
                    if var not in self.global_variables:
                        self.global_variables[var] = ctx.start.line
        return super().visitExpression(ctx)

    def visitMethodInvocation(self, ctx: JavaParser.MethodInvocationContext):
        parent_ctx = ctx.parentCtx
        is_assignment = isinstance(parent_ctx, JavaParser.AssignmentContext)
        if ctx.argumentList():
            argument_text = ctx.argumentList().getText()
            used_vars = self.extract_used_variables(argument_text)
            output_vars = []
            # ثبت متغیرها برای متدهای void یا System.out.println
            output_vars = [var for var in used_vars if
                           var not in self.method_parameters and
                           not self.is_keyword(var)]
            if self.current_method_is_void:
                for var in output_vars:
                    self.current_method_variables_used_in_calls.add(var)
            non_local_vars = [var for var in used_vars if
                              var not in self.defined_variables and
                              var not in self.local_variables and
                              var not in self.method_parameters and
                              not self.is_keyword(var)]
            if not is_assignment and non_local_vars:
                self.result.append({
                    "line": ctx.start.line,
                    "used_variables": non_local_vars
                })
            if "System.out.println" in ctx.getText() and output_vars:
                self.result.append({
                    "line": ctx.start.line,
                    "used_variables": output_vars,
                    "is_output": True
                })
        return super().visitMethodInvocation(ctx)

    def visitReturnStatement(self, ctx: JavaParser.ReturnStatementContext):
        if ctx.expression():
            return_expr = ctx.expression().getText()
            used_vars = self.extract_used_variables(return_expr)
            if used_vars:
                self.result.append({
                    "line": ctx.start.line,
                    "used_variables": used_vars
                })
        return super().visitReturnStatement(ctx)

    def extract_used_variables(self, expression: str):
        # حذف رشته‌های متنی (strings) از عبارت
        expression = re.sub(r'"(?:\\.|[^"\\])*"', '', expression)
        # پیدا کردن تمام شناسه‌ها
        all_identifiers = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', expression)
        result = []
        for ident in all_identifiers:
            # بررسی اینکه شناسه بخشی از یک دسترسی نقطه‌ای (مانند obj.field) یا فراخوانی متد (مانند readInt()) نباشد
            if f".{ident}" in expression or f"{ident}(" in expression:
                continue
            if not self.is_keyword(ident):
                result.append(ident)
        return list(set(result))

    @staticmethod
    @staticmethod
    def is_keyword(identifier: str):
        java_keywords = {
            "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const",
            "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float", "for",
            "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native", "new",
            "null", "package", "private", "protected", "public", "return", "short", "static", "strictfp",
            "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void", "volatile",
            "while", "true", "false",
            "Double", "Math", "String", "Integer", "Float", "Long", "Short", "Byte", "Character", "Boolean"
        }
        return identifier in java_keywords

    def get_analysis_results(self):
        return {
            "defined_variables": self.defined_variables,
            "global_variables": self.global_variables
        }


def main(input_file):
    # Parse the Java file
    input_stream = FileStream(input_file, encoding="utf-8")
    lexer = JavaLexer(input_stream)
    token_stream = CommonTokenStream(lexer)
    parser = JavaParser(token_stream)

    # Build the parse tree
    tree = parser.compilationUnit()

    # Analyze the Java code
    analyzer = JavaCodeAnalyzer()
    analyzer.visit(tree)

    # Print analysis results
    results = analyzer.get_analysis_results()

    # Print global variables with their usage
    print("\nGlobal Variables:")
    for result in analyzer.result:
        if "global_variable" in result:
            print(
                f"Line {result['line']}: Global Variable: {result['global_variable']}, Used Variables: {result['used_variables']}")

    # Print analysis results
    print("\nAnalysis Results:")
    for result in analyzer.result:
        if "method_name" in result and "parameters" in result:
            print(f"Line {result['line']}: Method {result['method_name']} with Parameters: {result['parameters']}")
        elif "method_name" in result and "void_method_output_variables" in result:
            print(
                f"Line {result['line']}: Method {result['method_name']}, Void Method Output Variables: {result['void_method_output_variables']}")
        elif "used_variables" in result:
            print(f"Line {result['line']}: Used Variables: {result['used_variables']}")
    # Create a dictionary for used variables by line
    used_variables_dict = {result["line"]: result["used_variables"] for result in analyzer.result if
                           "used_variables" in result}

    # Map each line to its corresponding method
    method_lines = {}
    for result in analyzer.result:
        if "method_name" in result:
            method_name = result["method_name"]
            start_line = result["line"]
            # Find the end of the method by looking for the next method or the end of the file
            end_line = None
            for next_result in analyzer.result:
                if "method_name" in next_result and next_result["line"] > start_line:
                    end_line = next_result["line"] - 1
                    break
            if end_line is None:
                end_line = float('inf')  # If no next method, assume end of file
            method_lines[method_name] = (start_line, end_line)

    # Group used variables by method
    method_used_variables = {}
    for line, used_vars in used_variables_dict.items():
        for method_name, (start_line, end_line) in method_lines.items():
            if start_line <= line <= end_line:
                if method_name not in method_used_variables:
                    method_used_variables[method_name] = []
                method_used_variables[method_name].append((line, used_vars))
                break

    # Filter out methods with only one line of used variables
    filtered_used_variables_dict = {}
    for method_name, lines in method_used_variables.items():
        if len(lines) > 1:
            for line, used_vars in lines:
                filtered_used_variables_dict[line] = used_vars

    # Print the filtered dictionary
    print("\nFiltered Used Variables Dictionary:")
    print(filtered_used_variables_dict)

    # print(filtered_used_variables_dict)
    return filtered_used_variables_dict


if __name__ == "__main__":
    input_file = "G:\\OpenUnderstand\\cfg_generator\\test\\f.java"
    main(input_file)
