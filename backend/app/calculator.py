"""Small scientific calculator interpreted from an explicit AST whitelist."""
import ast
import math
import operator


def calculate(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 200:
        raise ValueError("表达式须为 1–200 字符")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                  ast.Mod: operator.mod, ast.Pow: operator.pow}
    functions = {"sin": math.sin, "cos": math.cos, "tan": math.tan,
                 "sqrt": math.sqrt, "abs": abs, "radians": math.radians,
                 "degrees": math.degrees}
    constants = {"pi": math.pi}

    def checked(value):
        if type(value) not in {int, float}:
            raise ValueError("只支持实数计算")
        try:
            valid = math.isfinite(value) and abs(value) <= 1e15
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("计算结果超出安全范围")
        return value

    def walk(node, depth=0):
        if depth > 20:
            raise ValueError("表达式嵌套过深")
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            result = node.value
        elif isinstance(node, ast.Name) and node.id in constants:
            result = constants[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = walk(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            left, right = walk(node.left, depth + 1), walk(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1e6):
                raise ValueError("幂运算超出安全范围")
            result = operations[type(node.op)](left, right)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in functions and len(node.args) == 1 and not node.keywords):
            argument = walk(node.args[0], depth + 1)
            if node.func.id in {"sin", "cos", "tan"}:
                if abs(argument) > 1e6:
                    raise ValueError("三角函数弧度超出可靠计算范围")
                if node.func.id == "tan" and abs(math.cos(argument)) < 1e-12:
                    raise ValueError("tan 在该角度处未定义或数值不稳定")
            result = functions[node.func.id](argument)
        else:
            raise ValueError("仅支持数字、pi、四则/取模/有限幂及单参数 sin/cos/tan/sqrt/abs/radians/degrees")
        return checked(result)

    try:
        result = walk(ast.parse(expression, mode="eval").body)
    except (SyntaxError, ZeroDivisionError, OverflowError) as error:
        raise ValueError("表达式语法、除数或数值范围无效") from error
    return {"expression": expression, "result": result,
            "angle_convention": "sin/cos/tan 输入为弧度；角度值使用 radians(degrees_value) 转换",
            "verified_by": "受限 AST 数值与白名单数学函数运算"}
