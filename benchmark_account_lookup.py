import timeit
import random

# Generate dummy accounts
num_accounts = 10000
target_id = str(num_accounts - 1)  # Worst case scenario
target_name = f"Account {target_id}"

accounts = {
    'accounts': [{'id': str(i), 'displayName': f"Account {i}"} for i in range(num_accounts)]
}

def current_approach_id():
    cash_account_id = target_id
    target_account = None
    for acc in accounts.get('accounts', []):
        if str(acc.get('id')) == str(cash_account_id):
            target_account = acc
            break
    return target_account

def optimized_approach_id():
    cash_account_id = target_id
    target_account = next((acc for acc in accounts.get('accounts', []) if str(acc.get('id')) == str(cash_account_id)), None)
    return target_account

def current_approach_name():
    target_account = None
    for acc in accounts.get('accounts', []):
        if acc.get('displayName') == target_name:
            target_account = acc
            break
    return target_account

def optimized_approach_name():
    target_account = next((acc for acc in accounts.get('accounts', []) if acc.get('displayName') == target_name), None)
    return target_account

print("Benchmarking ID lookup...")
t1 = timeit.timeit(current_approach_id, number=1000)
print(f"Current approach: {t1:.6f} seconds")
t2 = timeit.timeit(optimized_approach_id, number=1000)
print(f"Optimized approach: {t2:.6f} seconds")

print("\nBenchmarking Name lookup...")
t3 = timeit.timeit(current_approach_name, number=1000)
print(f"Current approach: {t3:.6f} seconds")
t4 = timeit.timeit(optimized_approach_name, number=1000)
print(f"Optimized approach: {t4:.6f} seconds")
