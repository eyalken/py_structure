import os
import ast
import argparse
from collections import defaultdict, deque
from pathlib import Path

def get_module_name(root: str, file_path: str) -> str | None:
    root = os.path.abspath(root)
    file_path = os.path.abspath(file_path)
    if not file_path.endswith('.py') or not file_path.startswith(root):
        return None
    relative_path = os.path.relpath(file_path, root)
    if relative_path.startswith(".."):  # safety
        return None
    module= root.split(os.sep)[-1]+"." + ".".join(relative_path[:-3].split(os.sep))
    return module

def resolve_relative_import(caller_parts: list[str], level: int, module: str | None) -> str:
    if level > len(caller_parts):
        return '<invalid>'
    base = caller_parts[:-level]
    if module:
        base += module.split('.')
    return '.'.join(base)

def analyze_imports(root: str, file_path: str) -> list[tuple[str, str]]:
    result = []
    caller_module = get_module_name(root, file_path)
    if not caller_module:
        return result
    caller_parts = caller_module.split('.')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception:
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.append((caller_module, alias.name))

        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                resolved = resolve_relative_import(caller_parts, node.level, node.module)
                result.append((caller_module, resolved))

            elif node.module:
                for alias in node.names:
                    # Determine the full path of the imported module
                    full_module_path = os.path.join(root, *node.module.split('.'), f"{alias.name}.py")
                    full_package_path = os.path.join(root, *node.module.split('.'), alias.name, "__init__.py")
                    if os.path.isfile(full_module_path) or os.path.isfile(full_package_path):
                        full_module = f"{node.module}.{alias.name}"
                    else:
                        # Possibly importing an attribute or something not resolvable as a file
                        full_module = node.module
                    result.append((caller_module, full_module))
    return result



def collect_modules_and_imports(roots: list[str]) -> tuple[set[str], list[tuple[str, str]], dict[str, str]]:
    modules = set()
    files = []
    module_to_path = {}
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.endswith('.py'):
                    file_path = os.path.join(dirpath, filename)
                    module = get_module_name(root, file_path)
                    if module:
                        modules.add(module)
                        files.append((root, file_path))
                        module_to_path[module] = os.path.abspath(file_path)
    imports = []
    for root, file_path in files:
        imports.extend(analyze_imports(root, file_path))
    return modules, imports, module_to_path

def build_reverse_dep_graph(imports: list[tuple[str, str]]) -> dict[str, set[str]]:
    reverse_deps = defaultdict(set)
    for caller, imported in imports:
        reverse_deps[imported].add(caller)
    return reverse_deps

def trace_dependency_paths(reverse_deps: dict[str, set[str]], start_modules: set[str]) -> dict[str, list[str]]:
    paths = {}
    queue = deque([(mod, [mod]) for mod in start_modules])
    visited = set(start_modules)
    while queue:
        current, path = queue.popleft()
        for dependent in reverse_deps.get(current, []):
            if dependent not in visited:
                visited.add(dependent)
                paths[dependent] = path + [dependent]
                queue.append((dependent, path + [dependent]))
    return paths

def main():
    parser = argparse.ArgumentParser(
        description="Collect Python module import relationships",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--root", action="append", required=True, help="Root directory (can be used multiple times)")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["dep", "nodeps", "nodeps_verbose", "pkg_dep","not_pkg_dep", "outside_local_dir", "encapsulated_dir"],
        help=(
            "* nodeps: find py files which not depend in any file in root\n"
            "* nodeps_verbose: same as nodeps but prints table of external deps\n"
            "* dep: find files which depends on files in root\n"
            "* pkg_dep: find files which recursively depended in package\n"
            "* not_pkg_dep: find files which not depended (even recursively) in package\n"
            "* outside_local_dir: find files that import modules outside their own directory and subdirs\n"
            "* encapsulated_dir: list directories where all Python files depend only on their own dir/subdirs"
        )
    )
    parser.add_argument("package", nargs="?", help="Package name for 'pkg_dep' mode")

    args = parser.parse_args()
    all_modules, all_imports, module_to_path = collect_modules_and_imports(args.root)

    if args.mode == "dep":
        print("\nInternal Imports Found:")
        for caller, imported in all_imports:
            if imported in all_modules:
                print(f"{caller} imports {imported} --> {imported}")

    elif args.mode == "nodeps":
        has_deps = {caller for caller, imported in all_imports if imported in all_modules}
        no_deps = all_modules - has_deps
        print("\nModules with no internal dependencies:")
        for module in sorted(no_deps):
            print(module)

    elif args.mode == "nodeps_verbose":
        has_deps = {caller for caller, imported in all_imports if imported in all_modules}
        no_deps = all_modules - has_deps

        print("\nModules with no internal dependencies (external dependencies shown):")
        print(f"{'MODULE':<60} | {'EXTERNAL IMPORT'}")
        print("="*90)

        for caller in sorted(no_deps):
            externals = [imported for c, imported in all_imports if c == caller and imported not in all_modules]
            if not externals:
                print(f"{caller:<60} | -")
            else:
                for idx, ext in enumerate(externals):
                    if idx == 0:
                        print(f"{caller:<60} | {ext}")
                    else:
                        print(f"{'':<60} | {ext}")

    elif args.mode == "pkg_dep":
        if not args.package:
            print("Error: --mode pkg_dep requires a package name.")
            return

        reverse_deps = build_reverse_dep_graph(all_imports)
        direct_dependents = {
            caller for caller, imported in all_imports
            if imported == args.package or imported.startswith(f"{args.package}.")
        }

        paths = trace_dependency_paths(reverse_deps, direct_dependents)

        for module in sorted(paths):
            if module in direct_dependents:
                print(f"{module} (direct)")
            else:
                print(f"{module} (indirect)")
                print("  path:", " -> ".join(paths[module]))

        for module in sorted(direct_dependents - set(paths)):
            print(f"{module} (direct)")

    elif args.mode == "not_pkg_dep":
        if not args.package:
            print("Error: --mode not_pkg_dep requires a package name.")
            return

        reverse_deps = build_reverse_dep_graph(all_imports)
        direct_dependents = {
            caller for caller, imported in all_imports
            if imported == args.package or imported.startswith(f"{args.package}.")
        }

        paths = trace_dependency_paths(reverse_deps, direct_dependents)
        all_dependent = set(paths) | direct_dependents

        not_dependent = all_modules - all_dependent
        print(f"\nModules NOT dependent (even recursively) on package '{args.package}':")
        for module in sorted(not_dependent):
            print(module)

    elif args.mode == "outside_local_dir":
        outside_local = defaultdict(list)

        for caller, imported in all_imports:
            if imported in module_to_path and caller in module_to_path:
                caller_path = Path(module_to_path[caller])
                imported_path = Path(module_to_path[imported])
                try:
                    if not imported_path.resolve().relative_to(caller_path.parent.resolve()):
                        outside_local[caller].append(imported)
                except ValueError:
                    outside_local[caller].append(imported)

        print("\nFiles importing outside their local directory or subdirectories:")
        for caller, imports in sorted(outside_local.items()):
            caller_path = module_to_path[caller]
            print(f"{caller_path}")
            for imp in sorted(imports):
                imported_path = module_to_path.get(imp)
                if imported_path:
                    print(f"  \u21b3 {imported_path}")
                else:
                    print(f"  \u21b3 {imp} (not found in local module map)")

    elif args.mode == "encapsulated_dir":
        encapsulated = []

        for root_dir in args.root:
            for dirpath, _, filenames in os.walk(root_dir):
                dirpath_obj = Path(dirpath).resolve()
                local_modules = []
                local_outside_deps = []

                for filename in filenames:
                    if not filename.endswith(".py"):
                        continue
                    file_path = os.path.join(dirpath, filename)
                    module = get_module_name(root_dir, file_path)
                    if not module or module not in module_to_path:
                        continue

                    local_modules.append(module)
                    for caller, imported in all_imports:
                        if caller != module:
                            continue
                        if imported in module_to_path:
                            imported_path = Path(module_to_path[imported]).resolve()
                            try:
                                imported_path.relative_to(dirpath_obj)
                            except ValueError:
                                local_outside_deps.append((caller, imported))

                if local_modules and not local_outside_deps:
                    encapsulated.append(dirpath_obj)

        print("\nEncapsulated directories (only import within own directory tree):")
        for dir_path in sorted(encapsulated):
            print(dir_path)

if __name__ == "__main__":
    main()

