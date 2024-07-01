import pika

url = 'amqps://rabbitmq:ojp5seyp@rabbitmq-7yig.onrender.com:5672/'
params = pika.URLParameters(url)

try:
    connection = pika.BlockingConnection(params)
    print("Successfully connected to RabbitMQ")
    connection.close()
except Exception as e:
    print(f"Failed to connect to RabbitMQ: {e}")