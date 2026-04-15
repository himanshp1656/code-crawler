import asyncio, asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://postgres:S4TsSkfNMWSh48z@database-1.cvmm2q4e8zjp.ap-south-1.rds.amazonaws.com:5432/postgres')
    defs = await conn.fetchval('SELECT COUNT(*) FROM function_defs')
    branches = await conn.fetchval('SELECT COUNT(*) FROM function_branches')
    shared = await conn.fetchval('SELECT COUNT(*) FROM (SELECT def_id FROM function_branches GROUP BY def_id HAVING COUNT(*) > 1) x')
    print(f'function_defs:     {defs}')
    print(f'function_branches: {branches}')
    print(f'shared defs:       {shared}')
    await conn.close()

asyncio.run(main())
