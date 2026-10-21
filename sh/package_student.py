"""로컬 자격증명·실자료를 제외한 학생용 ZIP. 원본과 기존 ZIP을 덮어쓰지 않는다."""
from fnmatch import fnmatch
import os
from pathlib import Path
import sys
import tempfile
import zipfile


NAME = 'all_total_vue_student'
PRIVATE_DIRECTORIES = {
    '.git', '.venv', '__pycache__', '.aws', '.codex', '.agents', '.archify',
    '.pytest_cache', 'node_modules',
}
PRIVATE_PATTERNS = (
    '*.db', '*.db-*', '*.sqlite', '*.sqlite-*', '*.sqlite3', '*.sqlite3-*',
    '*.log', '*.pem', '*.key', '*.zip',
)


def excluded(relative):
    parts = relative.parts
    name = relative.name
    if any(p in PRIVATE_DIRECTORIES or p.startswith('.student-package-') for p in parts):
        return True
    if name == '.DS_Store' or name == '.env' or name.startswith('.env.'):
        return parts != ('.env.example',)
    if any(fnmatch(name, pattern) for pattern in PRIVATE_PATTERNS):
        return True
    if parts[:2] == ('data', 'research'):
        return True
    if parts[:3] == ('doc', 'finance', 'cases'):
        return True
    if parts[:2] in {('doc', 'image_usecase'), ('doc', 'image_usecase_openai')}:
        return True
    if parts[:3] == ('doc', 'finance', 'evaluation') and len(parts) == 4:
        return name.endswith('.json') or name == 'EXPECTED_RESULTS.md'
    return False


def make_archive(root, output):
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('기존 ZIP을 보존합니다. 다른 출력 경로를 지정해주세요.')
    count = 0
    # 출력과 같은 파일시스템에 준비한 뒤 배타적으로 게시한다.
    # 동시 실행 중 생긴 ZIP도 os.link가 덮어쓰지 않는다.
    with tempfile.TemporaryDirectory(prefix='.student-package-', dir=output.parent) as temporary:
        archive_path = Path(temporary) / 'package.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for folder, directories, files in os.walk(root, followlinks=False):
                directory = Path(folder)
                directories[:] = sorted(d for d in directories
                    if not (directory / d).is_symlink() and not excluded((directory / d).relative_to(root)))
                for name in sorted(files):
                    path = directory / name
                    relative = path.relative_to(root)
                    if path.is_symlink() or not path.is_file() or excluded(relative) or path.absolute() == output:
                        continue
                    archive.write(path, str(Path(NAME) / relative))
                    count += 1
        archive_path.chmod(0o600)
        os.link(archive_path, output)
    return count


def main():
    root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else root.parent / (NAME + '.zip')
    try:
        count = make_archive(root, output)
    except FileExistsError:
        print('기존 ZIP을 보존합니다. 다른 출력 경로를 지정해주세요.', file=sys.stderr)
        return 1
    print(f'생성됨: {output.absolute()} ({count}개 파일)')
    print('원본 파일은 유지하며 자격증명·DB·실자료·심볼릭 링크는 제외합니다.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
