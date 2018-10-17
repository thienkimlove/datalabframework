import os

from . import params
from . import data
from . import utils
from . import project

# purpose of engines
# abstract engine init, data read and data write
# and move this information to metadata

# it does not make the code fully engine agnostic though.

engines = dict()

class SparkEngine():
    def __init__(self, name, config):
        from pyspark import SparkContext, SparkConf
        from pyspark.sql import SQLContext

        here = os.path.dirname(os.path.realpath(__file__))

        submit_args = ''

        jars = []
        jars += config.get('jars', [])
        if jars:
            submit_jars = ' '.join(jars)
            submit_args = '{} --jars {}'.format(submit_args, submit_jars)

        packages = config.get('packages', [])
        if packages:
            submit_packages = ','.join(packages)
            submit_args = '{} --packages {}'.format(submit_args, submit_packages)

        submit_args = '{} pyspark-shell'.format(submit_args)

        # os.environ['PYSPARK_SUBMIT_ARGS'] = "--packages org.postgresql:postgresql:42.2.5 pyspark-shell"
        os.environ['PYSPARK_SUBMIT_ARGS'] = submit_args
        print('PYSPARK_SUBMIT_ARGS: {}'.format(submit_args))

        conf = SparkConf()
        if 'jobname' in config:
            conf.setAppName(config.get('jobname'))

        rmd = params.metadata()['providers']
        for v in rmd.values():
            if v['service'] == 'minio':
                conf.set("spark.hadoop.fs.s3a.endpoint", 'http://{}:{}'.format(v['hostname'],v.get('port',9000))) \
                    .set("spark.hadoop.fs.s3a.access.key", v['access']) \
                    .set("spark.hadoop.fs.s3a.secret.key", v['secret']) \
                    .set("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
                    .set("spark.hadoop.fs.s3a.path.style.access", True)
                break

        #set master
        conf.setMaster(config.get('master', 'local[*]'))

        #set log level fro spark
        sc = SparkContext(conf=conf)

        # pyspark set log level method
        # (this will not suppress WARN before starting the context)
        sc.setLogLevel("ERROR")

        self._ctx = SQLContext(sc)
        self.info = {'name': name, 'context':'spark', 'config': config}

    def context(self):
        return self._ctx

    def read(self, resource=None, path=None, provider=None, **kargs):
        md = data.metadata(resource, path, provider)
        if not md:
            print('no valid resource found')
            return

        pmd = md['provider']
        rmd = md['resource']

        url = md['url']
        print("resource url: {}".format(url))

        cache = pmd.get('read',{}).get('cache', False)
        cache = rmd.get('read',{}).get('cache', cache)

        repartition = pmd.get('read',{}).get('repartition', None)
        repartition = rmd.get('read',{}).get('repartition', repartition)

        coalesce = pmd.get('read',{}).get('coalesce', None)
        coalesce = rmd.get('read',{}).get('coalesce', coalesce)

        # override options on provider with options on resource, with option on the read method
        options = utils.merge(pmd.get('read',{}).get('options',{}), rmd.get('read',{}).get('options',{}))
        options = utils.merge(options, kargs)

        if pmd['service'] in ['local', 'hdfs', 'minio']:
            if pmd['format']=='csv':
                obj= self._ctx.read.csv(url, **options)
            if pmd['format']=='json':
                obj= self._ctx.read.option('multiLine',True).json(url, **options)
            if pmd['format']=='jsonl':
                obj= self._ctx.read.json(url, **options)
            elif pmd['format']=='parquet':
                obj= self._ctx.read.parquet(url, **options)

        elif pmd['service'] == 'sqlite':
            driver = "org.sqlite.JDBC"
            obj =  self._ctx.read \
                .format('jdbc') \
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .load(**options)

        elif pmd['service'] == 'mysql':
            driver = "com.mysql.cj.jdbc.Driver"
            obj =  self._ctx.read\
                .format('jdbc')\
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .option("user",pmd['username'])\
                .option('password',pmd['password'])\
                .load(**options)
        elif pmd['service'] == 'postgres':
            driver = "org.postgresql.Driver"
            obj =  self._ctx.read\
                .format('jdbc')\
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .option("user",pmd['username'])\
                .option('password',pmd['password'])\
                .load(**options)
        elif pmd['service'] == 'mssql':
            driver = "com.microsoft.sqlserver.jdbc.SQLServerDriver"
            obj = self._ctx.read\
                .format('jdbc')\
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .option("user",pmd['username'])\
                .option('password',pmd['password'])\
                .load(**options)
        elif pmd['service'] == 'mongodb':
            obj = self._ctx.read\
                .format('com.stratio.datasource.mongodb') \
                .option("host", pmd['hostname']) \
                .option("database",pmd['database'])\
                .option('collection',rmd['path']) \
                .load(**options)
        elif pmd['service'] == 'oracle':
            obj = self._ctx.read \
                .format("jdbc")\
                .option("url", "jdbc:oracle:thin:{0}/{1}@//{2}:{3}/SID".format(pmd['username'], pmd['password'], pmd['hostname'], pmd['port'])) \
                .option("dbtable", pmd['database']+'.'+rmd['path']) \
                .option("user", pmd['username'])\
                .option("password", pmd['password'])\
                .option("driver", "oracle.jdbc.driver.OracleDriver")\
                .load(**options)
        else:
            raise('downt know how to handle this')

        obj = obj.repartition(repartition) if repartition else obj
        obj = obj.coalesce(coalesce) if coalesce else obj
        obj = obj.cache() if cache else obj

        return obj

    def write(self, obj, resource=None, path=None, provider=None, **kargs):
        md = data.metadata(resource, path, provider)
        if not md:
            print('no valid resource found')
            return

        pmd = md['provider']
        rmd = md['resource']

        url = md['url']
        print("resource url: {}".format(url))

        cache = pmd.get('read',{}).get('cache', False)
        cache = rmd.get('read',{}).get('cache', cache)

        cache = pmd.get('write',{}).get('cache', False)
        cache = rmd.get('write',{}).get('cache', cache)

        repartition = pmd.get('write',{}).get('repartition', None)
        repartition = rmd.get('write',{}).get('repartition', repartition)

        coalesce = pmd.get('write',{}).get('coalesce', None)
        coalesce = rmd.get('write',{}).get('coalesce', coalesce)

        # override options on provider with options on resource, with option on the read method
        options = utils.merge(pmd.get('write',{}).get('options',{}), rmd.get('write',{}).get('options',{}))
        options = utils.merge(options, kargs)

        obj = obj.repartition(repartition) if repartition else obj
        obj = obj.coalesce(coalesce) if coalesce else obj
        obj = obj.cache() if cache else obj

        if pmd['service'] in ['local', 'hdfs', 'minio']:
            if pmd['format']=='csv':
                return obj.write.csv(url, **options)
            if pmd['format']=='json':
                return obj.write.option('multiLine',True).json(url, **options)
            if pmd['format']=='jsonl':
                return obj.write.json(url, **options)
            elif pmd['format']=='parquet':
                return obj.write.parquet(url, **options)
            else:
                print('format unknown')

        elif pmd['service'] == 'sqlite':
            driver = "org.sqlite.JDBC"
            return obj.write\
                .format('jdbc')\
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .save(**kargs)

        elif pmd['service'] == 'mysql':
            driver = "com.mysql.cj.jdbc.Driver"
            return obj.write\
                .format('jdbc')\
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .option("user",pmd['username'])\
                .option('password',pmd['password'])\
                .save(**kargs)

        elif pmd['service'] == 'postgres':
            driver = "org.postgresql.Driver"
            return obj.write\
                .format('jdbc')\
                .option('url', url)\
                .option("dbtable", rmd['path'])\
                .option("driver", driver)\
                .option("user",pmd['username'])\
                .option('password',pmd['password'])\
                .save(**kargs)
        elif pmd['service'] == 'mongodb':
            return obj.write \
                .format('com.stratio.datasource.mongodb') \
                .option("host", pmd['hostname']) \
                .option("database",pmd['database'])\
                .option('collection',rmd['path']) \
                .save(**kargs)
        elif pmd['service'] == 'oracle':
            return obj.write \
                .format("jdbc") \
                .option("url", "jdbc:oracle:thin:{0}/{1}@//{2}:{3}/SID".format(pmd['username'], pmd['password'], pmd['hostname'], pmd['port'])) \
                .option("dbtable", pmd['database']+'.'+rmd['path']) \
                .option("user", pmd['username']) \
                .option("password", pmd['password']) \
                .option("driver", "oracle.jdbc.driver.OracleDriver") \
                .save(**kargs)
        else:
            raise('downt know how to handle this')

def get(name):
    global engines

    #get
    engine = engines.get(name)

    if not engine:
        #create
        md = params.metadata()
        cn = md['engines'].get(name)
        config = cn.get('config', {})

        if cn['context']=='spark':
            engine = SparkEngine(name, config)
            engines[name] = engine

    return engine
