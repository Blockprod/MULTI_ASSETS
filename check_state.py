import pickle

try:
    with open("states/bot_state.pkl", "rb") as f:
        state = pickle.load(f)

    print("=== BOT STATE ANALYSIS ===")
    print(f"Nombre de paires: {len(state)}")
    print(f"\nPaires enregistrées: {list(state.keys())}")

    for pair, data in state.items():
        print(f"\n{pair}:")
        print(f"  - last_order_side: {data.get('last_order_side')}")
        print(f"  - entry_price: {data.get('entry_price')}")
        print(f"  - last_execution: {data.get('last_execution')}")
        print(f"  - execution_count: {data.get('execution_count', 0)}")
except Exception as e:
    print(f"Error: {e}")
