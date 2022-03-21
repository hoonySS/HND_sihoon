import sqlite3

from myutils.common import file_util


def open_database(db_file_path):
    print('Establishing a connection with a database at {}'.format(db_file_path))
    file_util.make_parent_dirs(db_file_path)
    connection = sqlite3.connect(db_file_path)
    cursor = connection.cursor()
    return connection, cursor


def close_database(connection, cursor):
    print('Closing a connection')
    cursor.close()
    connection.close()


def create_table(table_name, column_names, data_types, cursor, options=None):
    data_strs = ['{} {}'.format(column_name, data_type) for column_name, data_type in zip(column_names, data_types)]
    table_structure = ', '.join(data_strs)
    if options is not None and len(options) > 0:
        table_structure += ', ' + ', '.join(options)

    sql_statement = 'CREATE TABLE "{}" ({})'.format(table_name, table_structure)
    cursor.execute(sql_statement)


def load_table_names(cursor, db_name='sqlite_master'):
    cursor.execute('SELECT name FROM {} WHERE type="table"'.format(db_name))
    return [result[0] for result in cursor.fetchall()]


def load_data_from_table(column_names, table_id, cursor, condition=None):
    column_names_str = ', '.join(column_names)
    if condition is None:
        cursor.execute('SELECT {} FROM "{}"'.format(column_names_str, table_id))
    else:
        cursor.execute('SELECT {} FROM "{}" {}'.format(column_names_str, table_id, condition))
    return cursor.fetchall()
