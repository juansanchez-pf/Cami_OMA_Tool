# Use an official lightweight Python image
FROM python:3.9-slim

# Set the working directory
WORKDIR /app

# 1. Copy your requirements file 
# (Changed from requirements.txt to requirements to match your GitHub)
COPY requirements .

# 2. Install the libraries
RUN pip install --no-cache-dir -r requirements

# 3. Copy the rest of your code
COPY . .

# 4. Expose the port
EXPOSE 8080

# 5. Run the Streamlit app 
# (Changed to match your filename "Python-Code")
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
