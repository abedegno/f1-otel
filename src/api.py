#!/usr/bin/env python3
import os
import sqlite3

from flask import Flask, jsonify, request
from waitress import serve

app = Flask(__name__)
DATABASE = os.getenv("DATABASE", "/app/data/players.sqlite")

@app.route('/update_endpoint', methods=['POST'])
def update_endpoint():
    try:
        data = request.get_json()
        otlp_endpoint = data.get('otlp_endpoint')
        metrics_enabled = data.get('metrics_enabled', True)
        logs_enabled = data.get('logs_enabled', True)

        if not otlp_endpoint:
            return jsonify({'error': 'otlp_endpoint is required'}), 400

        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE endpoints SET otlp_endpoint = ?, metrics_enabled = ?, logs_enabled = ? WHERE id = 1",
                (otlp_endpoint, metrics_enabled, logs_enabled)
            )
            conn.commit()

        return jsonify({'message': 'Endpoint configuration updated successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=8503)
