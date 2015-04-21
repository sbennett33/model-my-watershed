"use strict";

var Backbone = require('../../shim/backbone'),
    App = require('../app');

var AnalyzeModel = Backbone.Model.extend({
    defaults: {
        area: 10,
        place: 'Philadelphia'
    }
});

var LayerModel = Backbone.Model.extend({

});

// Each layer returned from the analyze endpoint.
// Land, soil, etc.
var LayerCollection = Backbone.Collection.extend({
    url: '/api/analyze/',
    model: LayerModel
});

// Each category that makes up the areas of each layer
var LayerCategoryCollection = Backbone.Collection.extend({

});

module.exports = {
    AnalyzeModel: AnalyzeModel,
    LayerModel: LayerModel,
    LayerCollection: LayerCollection,
    LayerCategoryCollection: LayerCategoryCollection
};
