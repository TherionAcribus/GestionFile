import pika
import time


url = 'amqp://guest:guest@rabbitmq-7yig.onrender.com/'
params = pika.URLParameters(url)

# Ajoutez une boucle pour réessayer la connexion à RabbitMQ
for attempt in range(5):  # Réessayez 5 fois 
    try:
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(queue='hello')
        channel.basic_publish(exchange='', routing_key='hello', body='Hello World!')
        connection.close()
        print("Message sent to RabbitMQ")

    except pika.exceptions.AMQPConnectionError as e:
        print(f"Connection failed, retrying in 5 seconds... {e}")
        time.sleep(5)  # Attendez 5 secondes avant de réessayer

print("Failed to connect to RabbitMQ after 5 attempts")
