import pika
import os
import time

def test_connection():
    url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@rabbitmq:5672/')
    params = pika.URLParameters(url)
    
    for attempt in range(5):
        try:
            print(f"Attempt {attempt + 1} to connect to RabbitMQ at {url}")
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue='test')
            channel.basic_publish(exchange='', routing_key='test', body='Test Message')
            connection.close()
            print("Successfully connected to RabbitMQ")
            return True
        except pika.exceptions.AMQPConnectionError as e:
            print(f"Connection failed, retrying in 5 seconds... {e}")
            time.sleep(5)

    print("Failed to connect to RabbitMQ after 5 attempts")
    return False

if __name__ == "__main__":
    test_connection()