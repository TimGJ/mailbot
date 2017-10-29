import boto3

s3 = boto3.resource('s3')

for object in s3.Bucket('rotwang.org').objects.all():
    if object.key != 'AMAZON_SES_SETUP_NOTIFICATION':
        print(object.key)
