from PyInstaller.utils.hooks import collect_submodules, collect_data_files

datas = [('./*.py', 'ui')]
hiddenimports = collect_submodules('ui')
