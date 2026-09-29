FROM python:3.9-alpine

WORKDIR /opt/app

COPY ./requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY ./bot.py ./

# TOKEN and API_BASE_URL are supplied at run time (see README.md); nothing
# secret or deployment-specific is baked into the image.
CMD ["python3", "bot.py"]
