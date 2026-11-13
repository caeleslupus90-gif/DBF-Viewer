# DBF Viewer

A tool for viewing older .DBF files. Vibecoding using the Qwen3.7-Plus neural network.

## Features

- **GUI Interface** - Modern tkinter-based viewer with search, filtering, and editing
- **CLI Interface** - Command-line tool for batch operations and scripting
- **No external dependencies** - Pure Python implementation
- **Full DBF support** - Character, numeric, date, logical, memo fields
- **Encoding support** - Automatic detection and manual override (cp866, cp1251, cp1252, etc.)
- **Backup system** - Automatic .bak files before destructive operations

## Installation

```bash
git clone https://github.com/yourusername/dbf-viewer.git
cd dbf-viewer
```

No additional dependencies required. Python 3.7+ only.

## GUI Usage

Launch the graphical interface:

```bash
python dbf_gui.py
```

**Features:**
- Open and view DBF files with automatic column width calculation
- Search across all fields or specific columns
- Edit cell values (double-click)
- Add/delete/undelete records
- Export to CSV
- ZAP (clear all) and PACK (remove deleted) operations
- COPY TO with filter expressions
- APPEND FROM (DBF or CSV import)
- Column properties viewer (right-click header)

## CLI Usage

### Basic syntax

```bash
python dbf_cli.py <command> <file> [args] [options]
```

### Commands

#### `info` - Show file information
```bash
python dbf_cli.py info data.dbf
```
Displays: version, encoding, record count, field structure.

#### `show` - Display records
```bash
python dbf_cli.py show data.dbf
python dbf_cli.py show data.dbf -n 50 -o 100  # limit 50, offset 100
python dbf_cli.py show data.dbf -f "NAME,DATE,SUM"  # specific fields
python dbf_cli.py show data.dbf --hide-deleted  # skip deleted records
```

#### `get` - Get single record
```bash
python dbf_cli.py get data.dbf 42  # record #42
```

#### `set` - Update record
```bash
python dbf_cli.py set data.dbf 42 NAME="Иванов" SUM=1500 DATE=2024-01-15
```

#### `add` - Add new record
```bash
python dbf_cli.py add data.dbf NAME="Петров" SUM=2000
```

#### `delete` / `undelete` - Mark records
```bash
python dbf_cli.py delete data.dbf 10 11 12  # mark as deleted
python dbf_cli.py undelete data.dbf 10 11 12  # restore
```

#### `memo` - View memo field
```bash
python dbf_cli.py memo data.dbf 42 NOTES  # record #42, field NOTES
```

#### `export` - Export to CSV
```bash
python dbf_cli.py export data.dbf output.csv
python dbf_cli.py export data.dbf output.csv --hide-deleted
```

#### `zap` - Clear all records
```bash
python dbf_cli.py zap data.dbf --backup  # creates data.dbf.bak
```

#### `pack` - Remove deleted records
```bash
python dbf_cli.py pack data.dbf --backup
```

#### `copy` - Copy to new file with filter
```bash
python dbf_cli.py copy data.dbf filtered.dbf --filter "SUM>1000 AND NAME='Иванов'"
python dbf_cli.py copy data.dbf filtered.dbf --filter-py "SUM > 1000 and NAME.startswith('И')"
python dbf_cli.py copy data.dbf filtered.dbf --with-deleted  # include deleted
```

#### `append` - Import from file
```bash
python dbf_cli.py append data.dbf source.dbf
python dbf_cli.py append data.dbf source.csv --no-header --force --skip
```

### Options

- `-e, --encoding` - Force encoding (e.g., cp866, cp1251)
- `-n, --limit` - Number of records to show (default: 20)
- `-o, --offset` - Starting record offset (default: 0)
- `-f, --fields` - Comma-separated field names
- `--hide-deleted` - Skip deleted records
- `--backup` - Create .bak before write operations
- `--filter` - Simple filter expression
- `--filter-py` - Python expression filter
- `--with-deleted` - Include deleted in copy
- `--no-header` - CSV has no header row
- `--force` - Force conversion on import
- `--skip` - Skip problematic rows on import

## Examples

### View first 10 records
```bash
python dbf_cli.py show data.dbf -n 10
```

### Find records with SUM > 1000
```bash
python dbf_cli.py show data.dbf --filter "SUM>1000" -n 100
```

### Export filtered data
```bash
python dbf_cli.py copy data.dbf big_orders.dbf --filter "SUM>5000"
python dbf_cli.py export big_orders.dbf report.csv
```

### Import CSV data
```bash
python dbf_cli.py append data.dbf new_data.csv --force --skip
```

## File Format Support

- **dBASE III/IV** (.dbf, .dbt)
- **FoxPro/VFP** (.dbf, .fpt)
- **Character encodings**: cp437, cp850, cp866, cp1251, cp1252, and more

## License

MIT License - feel free to use in your projects.

## Credits

Developed with assistance from Qwen3.7-Plus neural network.